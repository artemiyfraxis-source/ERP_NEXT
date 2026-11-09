import { useEffect, useMemo, useState } from "react";
import { failureText, shippingApi, type Principal, type ProjectAddressee, type ProjectForecast, type Shipment, type UpdType } from "./api";
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
import { DISPATCH_STAGES, stageLabel, type DispatchStage } from "./dispatchStage";
import { DispatchCard } from "./DispatchCard";
import { canAct, hasFullAccess } from "./navigation";
import { dispatchBoard, kbKey, num, recentShipment, type Tab } from "./intakeStages";
import { dataRows, type DataRow } from "./dispatchPositionRows";
export { dataRows, type DataRow } from "./dispatchPositionRows";

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

/** УПД до «Готово» не требуется (владелец 29.10): на ранних этапах слова сервера про УПД не считаются «не хватает». */
const EARLY: Array<DispatchStage | null> = ["waiting", "production"];
export function stageGaps(stage: DispatchStage | null, gaps: string[] | undefined): string[] | undefined {
  return gaps && EARLY.includes(stage) ? gaps.filter((gap) => !/упд/i.test(gap)) : gaps;
}

/** ГРН партий строки: несколько партий — через запятую (`grns` сервера, иначе одна `grn`; у остатка — партии актов приёмки). */
export function grnText(row: DataRow, source?: IntakeRow): string {
  const line = row.shipment?.lines.find((item) => `${row.shipment!.id}:${item.id}` === row.id) as ({ grns?: string[] | null } & Shipment["lines"][number]) | undefined;
  const list = line?.grns?.length ? line.grns : line?.grn ? [line.grn] : row.shipment ? [] : [...new Set((source?.acts ?? []).map((act) => act.grn).filter(Boolean))];
  return list.length ? list.join(", ") : row.grn;
}

type UpdSource = { upd_type: UpdType | string | null; upd_number?: string | null; upd_url?: string | null };

/** `link` — файл ссылкой; в карточке-кнопке ссылки нет (кнопка внутри кнопки), файл откроется в окне отправки. */
function UpdCell({ shipment, link = true }: { shipment: UpdSource | null | undefined; link?: boolean }) {
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

const rowStageLabel = (row: DataRow) => row.stage === null && row.shipment?.status !== "Отменена" ? "Не набрано" : stageLabel(row.stage);

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
  const [tab, setTab] = useState<Tab>("packed");
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
    if (!shipment) return <span className="muted">—</span>;
    if (!number) return <span className="muted">Не оформлен{document === "dn" ? "а" : ""}</span>;
    const blocked = document === "dn" && ACTIVE.includes(shipment.status) && shipment.missing_data.length > 0;
    return <span className="ship-position-document" title={blocked ? `Не хватает: ${shipment.missing_data.join(", ")}` : `${label} ${number}`}>
      {canPrint ? <button type="button" className="link-button ship-pl" disabled={blocked}
        aria-label={`Печать ${document === "pl" ? "PL" : "DN"} ${number}`} onClick={(event) => { event.stopPropagation(); void printDocument(shipment, document); }}>{number}</button>
        : <span className="muted">{number}</span>}
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
  const board = useMemo(() => dispatchBoard(intake, shipments), [intake, shipments]);
  const gapsOf = (row: DataRow) => row.shipment ? dataGaps(row.shipment) : stageGaps(row.stage, cards?.[row.project]?.dispatch_gaps);
  const intakeByKb = useMemo(() => new Map((intake ?? []).map((item) => [kbKey(item), item])), [intake]);
  const sourceOf = (row: DataRow) => intakeByKb.get(kbKey(row));
  const rows = allRows.filter((row) => (tab === "all" || (row.stage === tab && board.inTab(row, tab) && (tab !== "shipped" || Boolean(row.shipment && recentShipment(row.shipment))))) && (!onlyGaps || (row.shipment ? dataGaps(row.shipment).length > 0
    : gapsOf(row) === undefined || Boolean(gapsOf(row)?.length) || (deciding(row) && !intake.some((item) => kbKey(item) === kbKey(row) && num(item.pack_permission_remaining) > 0)))));
  const current = open?.id.startsWith("project:") ? null : shipments.find((shipment) => shipment.id === open?.id) ?? null;
  const project = open?.id.startsWith("project:") ? open.row : null;
  const openRow = (row: DataRow) => setOpen({ id: row.shipment?.id ?? `project:${row.project}`, row });
  const decisionCell = (row: DataRow) => deciding(row) ? <PackDecision project={row.project} editable={canDecide}
    onReload={reload} /> : null;
  const dataCell = (row: DataRow) => {
    if (!row.shipment) {
      const gaps = gapsOf(row);
      return <span className={gaps === undefined ? "muted" : gaps.length ? "tone-warn" : "muted"}>
        {gaps === undefined ? "Данные проекта не загружены" : gaps.length ? `Не заполнено: ${gaps.join(", ")}` : EARLY.includes(row.stage) ? "Адрес, получатель и телефон заполнены" : "Данные получателя и УПД заполнены"}</span>;
    }
    return ACTIVE.includes(row.shipment.status) ? <Readiness source={row.shipment} gaps={dataGaps(row.shipment)} />
      : <span className="muted">{SENT.includes(row.shipment.status) ? sentNote(row.shipment) || "—" : "—"}</span>;
  };
  const forecastOf = (row: DataRow) => readiness(projectNames(row), forecasts, row.shipment?.deadline ?? null, row.shipment?.status ?? "Новая");
    const card = (row: DataRow) => <div className="data-card">
    <button type="button" className="data-card-main" onClick={() => openRow(row)} aria-label={`Открыть: ${row.shipment?.number ?? row.project}`}>
    <span className="data-card-head">
      {projectCell(row)}
      <StatusChip tone={row.stage ? STAGE_TONE[row.stage] : "neutral"}>{rowStageLabel(row)}</StatusChip>
    </span>
    {row.customer && <span className="data-card-customer">{row.customer}</span>}
    {row.shipment && <span>{dataCell(row)}</span>}
    <span className="data-card-due"><span className="muted">Дата отгрузки</span><span>{formatDate(row.shipment?.shipped_at) || "—"}</span></span>
    </button>
    <span className="data-card-upd">{documentCell(row.shipment, "pl")}{documentCell(row.shipment, "dn")}</span>
    {row.shipment && <span className="data-card-upd"><span className="muted">УПД</span><UpdCell shipment={row.shipment} /></span>}
  </div>;
  const qty = (row: DataRow) => row.quantity > 0 || row.shipment ? `${formatNumber(row.quantity)}${NBSP}${row.unit}` : "—";
  const deadlineOf = (row: DataRow) => row.shipment?.deadline ?? sourceOf(row)?.deadline ?? "";
  const stateCell = (row: DataRow) => <span className="ship-position-shipment"><span>{row.shipment?.status ?? "Не создана"}</span>{row.shipment && <span className="muted ship-sub">{row.shipment.number}</span>}</span>;
  const ALL: Record<string, DataColumn<DataRow>> = {
    project: { key: "project", title: "Проект", value: (row) => row.project, hidden: true },
    kb: { key: "kb", title: "КБ", value: (row) => row.item_index, width: 56, maxWidth: 72 },
    name: { key: "name", title: "Наименование", value: (row) => row.name, grow: true, minWidth: 160, wrap: true,
      render: (row) => <span className="ship-position-name" title={row.name}>{row.name}</span> },
    quantity: { key: "quantity", title: "Количество", value: (row) => row.quantity, numeric: true, width: 88, render: qty },
    ordered: { key: "ordered", title: "Заказано", value: (row) => num(sourceOf(row)?.needed) || row.quantity, numeric: true, width: 88,
      render: (row) => `${formatNumber(num(sourceOf(row)?.needed) || row.quantity)}${NBSP}${row.unit}` },
    launched: { key: "launched", title: "Запущено", value: (row) => num(sourceOf(row)?.launched), numeric: true, width: 88,
      render: (row) => `${formatNumber(num(sourceOf(row)?.launched))}${NBSP}${row.unit}` },
    rest: { key: "rest", title: "Остаток", hint: "Ещё не принято склада", value: (row) => num(sourceOf(row)?.remaining), numeric: true, width: 88,
      render: (row) => `${formatNumber(num(sourceOf(row)?.remaining))}${NBSP}${row.unit}` },
    term: { key: "term", title: "Срок", value: deadlineOf, width: 112, wrap: true,
      render: (row) => <><span className="muted ship-due">{formatDate(deadlineOf(row)) || "—"}</span><ReadyForecast forecast={readiness(projectNames(row), forecasts, deadlineOf(row) || null, row.shipment?.status ?? "Новая")} /></> },
    status: { key: "status", title: "Этап позиции", value: (row) => rowStageLabel(row), filter: true, width: 112,
      render: (row) => <span title={row.ownStage}><StatusChip tone={row.stage ? STAGE_TONE[row.stage] : "neutral"}>{rowStageLabel(row)}</StatusChip></span> },
    grn: { key: "grn", title: "ГРН партии", value: (row) => grnText(row, sourceOf(row)), width: 128, wrap: true, maxWidth: 200,
      render: (row) => <span className="ship-position-grn">{grnText(row, sourceOf(row)) || "—"}</span> },
    shipmentStatus: { key: "shipmentStatus", title: "Статус отправки", value: (row) => row.shipment?.status ?? "Не создана", width: 130, wrap: true, maxWidth: 160, render: stateCell },
    carrier: { key: "carrier", title: "ТК", hint: "Транспортная компания", value: (row) => row.shipment?.carrier ?? "", width: 96, wrap: true, maxWidth: 140,
      render: (row) => row.shipment?.carrier || "—" },
    track: { key: "track", title: "Трек", hint: "Трек-номер ТК", value: (row) => row.shipment?.track_number ?? "", width: 120, wrap: true, maxWidth: 160,
      render: (row) => row.shipment?.track_number || "—" },
    shippedAt: { key: "shippedAt", title: "Дата отгрузки", value: (row) => row.shipment?.shipped_at ?? "", width: 104, render: (row) => formatDate(row.shipment?.shipped_at) || "—" },
    pl: { key: "pl", title: "PL", hint: "Упаковочный лист", value: (row) => row.shipment?.pl_number ?? "", width: 108, wrap: true, maxWidth: 132, render: (row) => documentCell(row.shipment, "pl") },
    dn: { key: "dn", title: "DN", hint: "Отгрузочная ведомость", value: (row) => row.shipment?.dn_number ?? "", width: 112, wrap: true, maxWidth: 132, render: (row) => documentCell(row.shipment, "dn") },
    upd: { key: "upd", title: "УПД", value: (row) => row.shipment ? updText(row.shipment) : "", filter: true, width: 96, wrap: true, maxWidth: 120,
      render: (row) => <UpdCell shipment={row.shipment ?? cards?.[row.project]} /> },
    customer: { key: "customer", title: "Заказчик", value: (row) => row.customer, filter: true, hidden: true },
    gaps: { key: "gaps", title: "Данные", value: (row) => gapsOf(row)?.join(", ") || (row.shipment ? sentNote(row.shipment) : ""), hint: "Что внесено и чего не хватает; итог считает сервер", wrap: true, maxWidth: 520,
      hidden: true, render: dataCell },
  };
  // Столбцы по этапу, как на «Приёмке с производства»; «Все» — полный набор, там же «Этап позиции».
  const COLUMNS: Record<Tab, string[]> = {
    waiting: ["kb", "name", "ordered", "term"],
    production: ["kb", "name", "ordered", "launched", "rest", "term"],
    ready: ["kb", "name", "quantity", "grn", "upd", "shipmentStatus"],
    accepted: ["kb", "name", "quantity", "grn", "upd", "shipmentStatus"],
    packed: ["kb", "name", "quantity", "grn", "shipmentStatus", "carrier", "track", "pl", "dn", "upd"],
    to_ship: ["kb", "name", "quantity", "grn", "shipmentStatus", "carrier", "track", "pl", "dn", "upd"],
    shipped: ["kb", "name", "quantity", "grn", "shipmentStatus", "shippedAt", "carrier", "track", "pl", "dn", "upd"],
    all: ["kb", "name", "quantity", "status", "grn", "shipmentStatus", "shippedAt", "pl", "dn", "upd", "term"],
  };
  const columns = ["project", ...COLUMNS[tab], "customer", "gaps"].map((key) => ALL[key]);
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
      : <><div className="seg-tabs" role="group" aria-label="Быстрый отбор">{[...DISPATCH_STAGES, ["all", "Все"] as const].map(([key, text]) =>
        <button type="button" key={key} className={tab === key ? "on" : undefined} aria-pressed={tab === key} onClick={() => setTab(key)}>{text} · {board.counts[key]}</button>)}</div>
      {shipments.length >= 200 && <p className="muted">Загружены последние 200 отправок</p>}
      <DataTable key={tab} label="Данные отправок" storageKey={`${STORAGE_KEY}:${tab}`} rows={rows} columns={columns} rowKey={(row) => row.id}
        groupBy={(row) => row.project} groupLabel={groupLabel} groupCards
        onRowClick={openRow} exportName="Данные отправок" searchPlaceholder="Проект, КБ, наименование, ГРН или № отправки"
        searchText={(row) => [row.shipment?.number, row.name, row.grn, row.item_index]}
        cards={view === "cards" ? card : undefined}
        toolbar={<><span className="view-switch" role="group" aria-label="Вид списка">
          {VIEWS.map(([key, label]) => <button type="button" key={key} className="secondary small" aria-pressed={view === key} onClick={() => chooseView(key)}>{label}</button>)}
        </span></>}
        emptyText="В этом разделе пусто — другие этапы под чипами выше." /></>}
    {current && <ShipmentDataWindow key={current.id} shipment={current} stage={open?.row.stage} canEdit={canEdit} canCorrectHistory={hasFullAccess(principal) && !principal.viewing} close={() => setOpen(null)} onSaved={reload} />}
    {project && <DispatchCard key={project.id} project={project.project} customer={project.customer} stage={project.stage ?? undefined} canEdit={canEdit} close={() => setOpen(null)} onSaved={() => void reload()} />}
  </section>;
}
