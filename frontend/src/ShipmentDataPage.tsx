import { useEffect, useMemo, useState, type ReactNode } from "react";
import { failureText, shippingApi, type ItemAddressee, type Principal, type ProjectAddressee, type ProjectForecast, type Shipment } from "./api";
import "./shipping.css";
import { intakeApi, type IntakeRow } from "./productionIntakeApi";
import { cellContent, DataTable, type DataColumn } from "./DataTable";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";
import { ShipmentDataWindow, updLabel } from "./ShipmentData";
import { PackDecision, readiness } from "./shippingCells";
import { formatDay, formatDayMonth, formatNumber, NBSP } from "./format";
import { useCanPrint } from "./rights";
import { openShippingForms } from "./shippingPrint";
import { DISPATCH_STAGES, stageLabel, type DispatchStage } from "./dispatchStage";
import { DispatchCard } from "./DispatchCard";
import { ItemDeliveryWindow } from "./ProjectData";
import { canAct, hasFullAccess } from "./navigation";
import { dispatchBoard, kbKey, num, recentShipment, type Tab } from "./intakeStages";
import { actShares, dataRows, type DataRow } from "./dispatchPositionRows";
import { orderShipmentRows, shipmentGroupKey, shipmentSequence } from "./ShipmentGroupHeader";
import { shipmentSearch } from "./shipmentSearch";
import { isPickup, needsTrack, termsText } from "./carriers";
export { dataRows, type DataRow } from "./dispatchPositionRows";

/* «Данные отправок» (владелец 28.10.2026, раздел «Менеджмент»): менеджер (Ульяна Ладутько) вносит данные для
   отгрузки — тип доставки, перевозчик, адрес, получателя, телефон, УПД. Кладовщик упаковывает, а без этих данных
   отправить не может. Список — этапы общей лестницы (`dispatchStage.ts`: В ожидании · В производстве · Готово · Принято · Упаковано · На отгрузку · Отгружено); проекты без живой отправки — своими строками: данные проекта можно внести заранее, новые
   отправки берут их из карточки. По умолчанию открыт этап «Упаковано» — упакованное ждёт её данных, чтобы уехать;
   «Только с недостающими данными» — отдельный переключатель поверх любого этапа. */

const ACTIVE = ["Новая", "Собрана", "Упакована"];
const STORAGE_KEY = "shipment-data-positions-v5";

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

/** ГРН партий строки: несколько партий — через запятую (`grns` сервера, иначе одна `grn`). У остатка на складе — только
 *  партии, где ещё что-то лежит (`actShares`): ГРН уже уехавшей партии сюда не попадает (владелец 12.11). */
export function grnText(row: DataRow, source?: IntakeRow, shipments: Shipment[] = []): string {
  const line = row.shipment?.lines.find((item) => `${row.shipment!.id}:${item.id}` === row.id) as ({ grns?: string[] | null } & Shipment["lines"][number]) | undefined;
  const list = line?.grns?.length ? line.grns : line?.grn ? [line.grn] : row.shipment ? [] : [...new Set((source ? actShares(source, shipments) : []).map((share) => share.grn).filter(Boolean))];
  return list.length ? list.join(", ") : row.grn;
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
  const [itemCards, setItemCards] = useState<Record<string, ItemAddressee[]>>({});
  const [open, setOpen] = useState<{ id: string; row: DataRow } | null>(null);
  const [onlyGaps, setOnlyGaps] = useState(false);
  const [deliveryEdit, setDeliveryEdit] = useState<{ project: string; items: ItemAddressee[] } | null>(null);
  const [tab, setTab] = useState<Tab>("packed");
  const [forecasts, setForecasts] = useState<Record<string, ProjectForecast>>({});
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
    </span>;
  };

  const reload = async () => {
    try {
      const [shipmentResult, intakeResult] = await Promise.all([shippingApi.shipments(), intakeApi.rows()]);
      setShipments(shipmentResult.shipments); setIntake(intakeResult.rows); setError("");
      const projects = [...new Set(dataRows(shipmentResult.shipments, intakeResult.rows).filter((row) => !row.shipment).map((row) => row.project))];
      setCards(null); setItemCards({}); setCardsError("");
      try {
        const result = projects.length ? await shippingApi.addressees(projects) : { addressees: [] };
        setCards(Object.fromEntries(result.addressees.map((card) => [card.project, card])));
        const items: Record<string, ItemAddressee[]> = {};
        const pending = [...projects];
        await Promise.all(Array.from({ length: Math.min(4, pending.length) }, async () => {
          for (let project = pending.pop(); project !== undefined; project = pending.pop()) {
            items[project] = (await shippingApi.itemAddressees(project)).items ?? [];
          }
        }));
        setItemCards(items);
      } catch (reason) { setCardsError(failureText(reason, "Не удалось загрузить данные проектов")); }
    } catch (reason) { setError(failureText(reason, "Не удалось загрузить отправки")); }
    finally { setLoaded(true); }
  };
  useEffect(() => { void reload(); }, []);
  // Прогноз цеха — подсказка к сроку: не пришёл (нет связи, нет данных) — строки остаются без него.
  useEffect(() => { void shippingApi.forecast().then((answer) => setForecasts(answer?.projects ?? {}), () => undefined); }, []);

  const allRows = useMemo(() => dataRows(shipments, intake), [shipments, intake]);
  const board = useMemo(() => dispatchBoard(intake, shipments), [intake, shipments]);
  const itemOf = (row: DataRow) => itemCards[row.project]?.find((item) => item.item_index === row.item_index);
  const text = (value: string | null | undefined) => value?.trim() && !/^[\s\-‐-―−_.]*$/.test(value) ? value.trim() : "";
  const contact = (value: string | null | undefined) => text(value).toLowerCase() === "самовывоз" ? "" : text(value);
  const deliveryOf = (row: DataRow) => {
    const data = row.shipment ?? { ...cards?.[row.project], ...itemOf(row)?.effective };
    return { ...data, receiver: contact(data.receiver), phone: contact(data.phone), shipping_point: contact(data.shipping_point) };
  };
  const pickupOf = (row: DataRow) => isPickup(deliveryOf(row).carrier ?? "") || deliveryOf(row).terms === "САМОВЫВОЗ";
  const gapsOf = (row: DataRow) => {
    if (row.shipment) return dataGaps(row.shipment);
    if (!itemOf(row)) return stageGaps(row.stage, cards?.[row.project]?.dispatch_gaps);
    const data = deliveryOf(row);
    const gaps = [
      !data.terms && "условия доставки", !data.receiver && "организация", !data.phone && "телефон получателя",
      !pickupOf(row) && !data.shipping_point && "адрес доставки",
      data.terms === "ДОСТАВКА ТК" && !pickupOf(row) && !text(data.carrier) && "ТК",
      !data.upd_type && "вариант УПД",
      data.upd_type && data.upd_type !== "БЕЗ УПД" && !text(data.upd_number) && "№ УПД",
      data.upd_type === "БУМАГА" && !text(data.upd_url) && "файл УПД",
    ].filter((gap): gap is string => typeof gap === "string");
    return stageGaps(row.stage, gaps);
  };
  const intakeByKb = useMemo(() => new Map((intake ?? []).map((item) => [kbKey(item), item])), [intake]);
  const sourceOf = (row: DataRow) => intakeByKb.get(kbKey(row));
  const tabRows = allRows.filter((row) => tab === "all" || row.stage === tab && board.inTab(row, tab)
    && (tab !== "shipped" || Boolean(row.shipment && recentShipment(row.shipment))));
  const rows = orderShipmentRows(tabRows.filter((row) => !onlyGaps || (row.shipment ? dataGaps(row.shipment).length > 0
    : gapsOf(row) === undefined || Boolean(gapsOf(row)?.length) || (deciding(row) && !intake.some((item) => kbKey(item) === kbKey(row) && num(item.pack_permission_remaining) > 0)))));
  const current = open?.id.startsWith("project:") ? null : shipments.find((shipment) => shipment.id === open?.id) ?? null;
  const project = open?.id.startsWith("project:") ? open.row : null;
  const openRow = (row: DataRow) => setOpen({ id: row.shipment?.id ?? `project:${row.project}`, row });
  const decisionCell = (row: DataRow) => deciding(row) ? <PackDecision project={row.project} editable={canDecide}
    onReload={reload} /> : null;
  const empty = <span className="muted">—</span>;
  const field = (value: string | null | undefined, required = true) => value?.trim() && !/^[\s\-‐-―−_.]*$/.test(value)
    ? value : required ? <span className="tone-warn">не заполнено</span> : empty;
  const updRequired = (row: DataRow) => !EARLY.includes(row.stage);
  const needsUpdDocument = (row: DataRow) => updRequired(row) && deliveryOf(row).upd_type !== "БЕЗ УПД";
  const qty = (row: DataRow) => row.quantity > 0 || row.shipment ? `${formatNumber(row.quantity)}${NBSP}${row.unit}` : "—";
  const deadlineOf = (row: DataRow) => row.shipment?.deadline ?? sourceOf(row)?.deadline ?? "";
  const sequence = shipmentSequence(allRows);
  const forecastOf = (row: DataRow) => readiness(projectNames(row), forecasts, deadlineOf(row) || null, row.shipment?.status ?? "Новая");
  const commonForecasts = new Map<string, ReturnType<typeof forecastOf>>();
  for (const row of tabRows) {
    const forecast = forecastOf(row);
    if (!commonForecasts.has(row.project)) commonForecasts.set(row.project, forecast);
    else {
      const previous = commonForecasts.get(row.project);
      commonForecasts.set(row.project, previous?.ready && previous.ready === forecast?.ready && previous.late === forecast.late
        ? previous : null);
    }
  }
  const trackGaps = (row: DataRow) => {
    if (!row.shipment && gapsOf(row) === undefined) return "Данные проекта не загружены";
    const gaps = row.shipment ? [...row.shipment.missing_data, ...row.shipment.missing_ship] : gapsOf(row) ?? [];
    // Delivery and document blanks already have their own cells; retain independent blockers.
    return [...new Set(gaps)].filter((gap) => !/^(условия доставки|способ доставки|организация|получатель|телефон(?: получателя)?|адрес(?: доставки)?|тк|перевозчик|трек(?:-номер)?|(?:вариант|тип|номер|№|дата|файл) упд)$/i.test(gap)).join(", ");
  };
  const ALL: Record<string, DataColumn<DataRow>> = {
    kb: { key: "kb", title: "КБ", value: (row) => row.item_index, width: 128 },
    name: { key: "name", title: "Наименование", value: (row) => row.name, grow: true, minWidth: 180, maxWidth: 280, wrap: true,
      render: (row) => <span title={row.name}>{row.name}</span> },
    quantity: { key: "quantity", title: "Количество", value: (row) => row.quantity, numeric: true, width: 96, render: qty },
    ordered: { key: "ordered", title: "Заказано", value: (row) => num(sourceOf(row)?.needed) || row.quantity, numeric: true, width: 88,
      render: (row) => `${formatNumber(num(sourceOf(row)?.needed) || row.quantity)}${NBSP}${row.unit}` },
    launched: { key: "launched", title: "Запущено", value: (row) => num(sourceOf(row)?.launched), numeric: true, width: 88,
      render: (row) => `${formatNumber(num(sourceOf(row)?.launched))}${NBSP}${row.unit}` },
    rest: { key: "rest", title: "Остаток", hint: "Ещё не принято склада", value: (row) => num(sourceOf(row)?.remaining), numeric: true, width: 88,
      render: (row) => `${formatNumber(num(sourceOf(row)?.remaining))}${NBSP}${row.unit}` },
    action: { key: "action", title: "Действие", value: () => "", width: 112,
      render: (row) => <button type="button" className="secondary small" aria-label={`Открыть данные ${row.project} ${row.item_index}`}
        onClick={(event) => {
          event.stopPropagation();
          // Строка без отправки показывает доставку этой КБ — «Изменить» правит её, а не карточку проекта.
          const item = !row.shipment && canEdit ? itemOf(row) : undefined;
          if (item) setDeliveryEdit({ project: row.project, items: [item] }); else openRow(row);
        }}>{canEdit ? "Изменить" : "Открыть"}</button> },
    grn: { key: "grn", title: "ГРН", value: (row) => grnText(row, sourceOf(row), shipments), width: 144, wrap: true, maxWidth: 200 },
    project: { key: "project", title: "Проект", value: (row) => row.project, width: 112 },
    customer: { key: "customer", title: "Заказчик", value: (row) => row.customer, filter: true, width: 160, wrap: true, maxWidth: 200 },
    shipment: { key: "shipment", title: "Отправка", value: (row) => row.shipment?.number ?? "", width: 168,
      render: (row) => row.shipment ? <button type="button" className="link-button" aria-label={`Данные отправки ${row.shipment.number}`}
        onClick={(event) => { event.stopPropagation(); openRow(row); }}>{row.shipment.number}</button> : empty },
    sequence: { key: "sequence", title: "Отгрузка", value: (row) => sequence.get(shipmentGroupKey(row)) ?? "", width: 88 },
    shipmentStatus: { key: "shipmentStatus", title: "Статус", value: (row) => row.shipment?.status ?? "Не создана", filter: true, width: 112,
      render: (row) => row.shipment ? <StatusChip status={row.shipment.status} /> : "Не создана" },
    status: { key: "status", title: "Этап позиции", value: rowStageLabel, filter: true, width: 112,
      render: (row) => <StatusChip tone={row.stage ? STAGE_TONE[row.stage] : "neutral"}>{rowStageLabel(row)}</StatusChip> },
    createdAt: { key: "createdAt", title: "Создана", value: (row) => row.shipment?.created_at, width: 112, render: (row) => formatDay(row.shipment?.created_at) || "—" },
    term: { key: "term", title: "Срок", value: deadlineOf, width: 112, render: (row) => formatDay(deadlineOf(row)) || "—" },
    shippedAt: { key: "shippedAt", title: "Отгружена", value: (row) => row.shipment?.shipped_at, width: 112, render: (row) => formatDay(row.shipment?.shipped_at) || "—" },
    terms: { key: "terms", title: "Способ доставки", value: (row) => pickupOf(row) ? "Самовывоз" : termsText(deliveryOf(row).terms), width: 144,
      render: (row) => field(pickupOf(row) ? "Самовывоз" : termsText(deliveryOf(row).terms)) },
    carrier: { key: "carrier", title: "ТК", value: (row) => deliveryOf(row).carrier, width: 112,
      render: (row) => pickupOf(row) ? empty : field(deliveryOf(row).carrier) },
    track: { key: "track", title: "Трек", value: (row) => row.shipment?.track_number, width: 128,
      render: (row) => field(row.shipment?.track_number, Boolean(row.shipment && needsTrack(row.shipment))) },
    receiver: { key: "receiver", title: "Организация", value: (row) => deliveryOf(row).receiver, width: 160, wrap: true, maxWidth: 220,
      render: (row) => field(deliveryOf(row).receiver) },
    contact: { key: "contact", title: "Получатель", value: (row) => deliveryOf(row).contact, width: 160, wrap: true, maxWidth: 220,
      render: (row) => field(deliveryOf(row).contact, false) },
    phone: { key: "phone", title: "Телефон", value: (row) => deliveryOf(row).phone, width: 144,
      render: (row) => field(deliveryOf(row).phone) },
    address: { key: "address", title: "Адрес", value: (row) => deliveryOf(row).shipping_point, width: 200, wrap: true, maxWidth: 280,
      render: (row) => pickupOf(row) ? empty : field(deliveryOf(row).shipping_point) },
    upd: { key: "upd", title: "УПД", value: (row) => updLabel(deliveryOf(row).upd_type), filter: true, width: 96,
      render: (row) => field(updLabel(deliveryOf(row).upd_type), updRequired(row)) },
    updNumber: { key: "updNumber", title: "№ УПД", value: (row) => deliveryOf(row).upd_number, width: 112,
      render: (row) => needsUpdDocument(row) ? field(deliveryOf(row).upd_number) : empty },
    updDate: { key: "updDate", title: "Дата УПД", value: (row) => deliveryOf(row).upd_date, width: 112,
      render: (row) => formatDay(deliveryOf(row).upd_date) || "—" },
    updFile: { key: "updFile", title: "Файл УПД", value: (row) => deliveryOf(row).upd_url, width: 112,
      render: (row) => deliveryOf(row).upd_url ? <a href={deliveryOf(row).upd_url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>файл</a>
        : needsUpdDocument(row) ? <span className="tone-warn">без файла</span> : empty },
    pl: { key: "pl", title: "PL", value: (row) => row.shipment?.pl_number, width: 128, render: (row) => documentCell(row.shipment, "pl") },
    dn: { key: "dn", title: "DN", value: (row) => row.shipment?.dn_number, width: 128, render: (row) => documentCell(row.shipment, "dn") },
    trackGaps: { key: "trackGaps", title: "Ждём для трека", value: trackGaps, width: 200, wrap: true, maxWidth: 280,
      render: (row) => trackGaps(row) ? <span className="tone-warn">{trackGaps(row)}</span> : null },
    forecast: { key: "forecast", title: "Прогноз готовности", value: (row) => forecastOf(row)?.ready,
      width: 144, render: (row) => { const forecast = forecastOf(row);
        return forecast && !commonForecasts.get(row.project) ? <span className={forecast.late ? "tone-danger" : undefined}>{formatDay(forecast.ready)}</span> : null; } },
  };
  const leading = tab === "waiting" ? ["kb", "name", "ordered", "action"]
    : tab === "production" ? ["kb", "name", "ordered", "launched", "rest", "action"] : ["kb", "name", "quantity", "action"];
  const quantities = new Set(["quantity", "ordered", "launched", "rest"]);
  const hidden = new Set(["project", "customer", "shipment", "sequence", "status", "createdAt", "term", ...(tab === "shipped" ? [] : ["shippedAt"])]);
  const shipmentColumns = new Set(["shipment", "sequence", "shipmentStatus", "createdAt", "term", "shippedAt", "terms", "carrier", "track", "trackGaps", "receiver", "contact", "phone", "address", "upd", "updNumber", "updDate", "updFile", "pl", "dn"]);
  const columns = [...leading, ...Object.keys(ALL).filter((key) => !leading.includes(key) && !quantities.has(key))].map((key) => {
    const column = ALL[key];
    return { ...column, hidden: hidden.has(key), export: true,
      render: (row: DataRow) => key === "project" || key === "customer" || (row.shipment && (shipmentColumns.has(key) || key === "status")) ? null
        : column.render ? column.render(row) : cellContent(column, column.value(row)) };
  });
  const groupParentLabel = (_key: string, groupRows: DataRow[]) => {
    const source = groupRows.find(deciding) ?? groupRows.find((row) => !row.shipment) ?? groupRows[0];
    const decision = groupRows.find(deciding);
    const forecast = commonForecasts.get(source.project);
    return <span className="ship-position-group">
      <button type="button" className="link-button ship-position-group-title" onClick={(event) => { event.stopPropagation(); setOpen({ id: `project:${source.project}`, row: source }); }}><strong>{source.project}</strong></button>
      {source.customer && <span>· {source.customer}</span>}
      {forecast && <span className={forecast.late ? "tone-danger" : undefined}>· готовность {formatDayMonth(forecast.ready)}</span>}
      {canEdit && <button type="button" className="secondary small" aria-label={`Изменить данные отправки ${source.project}`}
        onClick={(event) => { event.stopPropagation(); setOpen({ id: `project:${source.project}`, row: source }); }}>Изменить данные проекта</button>}
      {decision && decisionCell(decision)}
    </span>;
  };
  const groupCells = (key: string, groupRows: DataRow[], _filtered: DataRow[], visibleColumns: DataColumn<DataRow>[]) => {
    const source = groupRows[0];
    if (!source.shipment) return null;
    const cells: Partial<Record<string, ReactNode>> = {};
    for (const columnKey of shipmentColumns) {
      const column = ALL[columnKey];
      cells[columnKey] = column.render ? column.render(source) : cellContent(column, column.value(source));
    }
    const visible = new Set(visibleColumns.map((column) => column.key));
    cells[visible.has("shipment") ? "shipment" : "name"] = ALL.shipment.render!(source);
    cells[visible.has("sequence") ? "sequence" : "kb"] = `Отгрузка ${sequence.get(key)}`;
    const totals = new Map<string, number>();
    for (const row of allRows) if (shipmentGroupKey(row) === key) totals.set(row.unit, (totals.get(row.unit) ?? 0) + row.quantity);
    cells.quantity = [...totals].map(([unit, amount]) => `${formatNumber(amount)}${NBSP}${unit}`).join(", ");
    cells.action = <button type="button" className="secondary small" aria-label={`Изменить отправку ${source.shipment.number}`}
      onClick={() => openRow(source)}>{canEdit ? "Изменить" : "Открыть"}</button>;
    return cells;
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
        groupBy={shipmentGroupKey} groupParent={(row) => row.project} groupParentLabel={groupParentLabel} groupCells={groupCells} groupSort
        onRowClick={openRow} exportName="Данные отправок" searchPlaceholder="Проект, КБ, наименование, ГРН, организация, получатель, УПД или трек"
        searchText={(row) => row.shipment ? shipmentSearch({ ...row.shipment,
          lines: row.shipment.lines.filter((line) => row.id === `${row.shipment!.id}:${line.id}`),
        }) : Object.values(deliveryOf(row)).filter((value): value is string => typeof value === "string")}
        emptyText="В этом разделе пусто — другие этапы под чипами выше." /></>}
    {current && <ShipmentDataWindow key={current.id} shipment={current} stage={open?.row.stage} canEdit={canEdit} canCorrectHistory={hasFullAccess(principal) && !principal.viewing} close={() => setOpen(null)} onSaved={reload} />}
    {deliveryEdit && <ItemDeliveryWindow project={deliveryEdit.project} items={deliveryEdit.items} close={() => setDeliveryEdit(null)} onSaved={reload} />}
    {project && <DispatchCard key={project.id} project={project.project} customer={project.customer} stage={project.stage ?? undefined} canEdit={canEdit} close={() => setOpen(null)} onSaved={() => void reload()} />}
  </section>;
}
