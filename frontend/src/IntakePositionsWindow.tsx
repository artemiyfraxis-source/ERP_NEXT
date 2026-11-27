import { useMemo, useState } from "react";
import { failureText, shippingApi, type ItemAddressee, type ProjectAddressee, type ProjectForecast, type Shipment } from "./api";
import { cellContent, DataTable, type DataColumn } from "./DataTable";
import { stageLabel, type DispatchStage } from "./dispatchStage";
import { actShares, dataRows, type DataRow } from "./dispatchPositionRows";
import { isPickup, needsTrack, termsText } from "./carriers";
import { formatDay, formatNumber, NBSP } from "./format";
import { kbKey, num } from "./intakeStages";
import { PageWindow } from "./PageWindow";
import { readiness } from "./shippingCells";
import { intakeApi, type IntakeRow } from "./productionIntakeApi";
import { useCanPrint } from "./rights";
import { orderShipmentRows, shipmentGroupKey, shipmentSequence } from "./ShipmentGroupHeader";
import { updLabel } from "./ShipmentData";
import { shipmentSearch } from "./shipmentSearch";
import { openShippingForms } from "./shippingPrint";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";

/* «Все позиции» — окно из шапки доски «Приёмка с производства» (владелец 27.11): бывшая страница «Данные отправок»
   одной таблицей — все КБ проектов и все отправки, колонки двух прежних экранов без повторов, фильтры колонок, поиск и
   Excel. Номера «ОТГ-…» здесь нет: отправку называют проект, КБ и PL. */

const ACTIVE = ["Новая", "Собрана", "Упакована"];
const STORAGE_KEY = "intake-positions:v1";

/** Что не хватает живой отправке (слова сервера). Уехавшие могли уехать осознанно без данных: для них «не хватает» не считается. */
export function dataGaps(shipment: Shipment): string[] {
  return ACTIVE.includes(shipment.status) ? shipment.missing_data : [];
}

/** УПД до «Готово» не требуется (владелец 29.10): на ранних этапах слова сервера про УПД не считаются «не хватает». */
const EARLY: Array<DispatchStage | null> = ["waiting", "production"];
export function stageGaps(stage: DispatchStage | null, gaps: string[] | undefined): string[] | undefined {
  return gaps && EARLY.includes(stage) ? gaps.filter((gap) => !/упд/i.test(gap)) : gaps;
}

/** ГРН партий строки: несколько партий — через запятую. У остатка на складе — только партии, где ещё что-то лежит. */
export function grnText(row: DataRow, source?: IntakeRow, shipments: Shipment[] = []): string {
  const line = row.shipment?.lines.find((item) => `${row.shipment!.id}:${item.id}` === row.id) as ({ grns?: string[] | null } & Shipment["lines"][number]) | undefined;
  const list = line?.grns?.length ? line.grns : line?.grn ? [line.grn] : row.shipment ? [] : [...new Set((source ? actShares(source, shipments) : []).map((share) => share.grn).filter(Boolean))];
  return list.length ? list.join(", ") : row.grn;
}

const rowStageLabel = (row: DataRow) => row.stage === null && row.shipment?.status !== "Отменена" ? "Не набрано" : stageLabel(row.stage);
const STAGE_TONE: Record<DispatchStage, Tone> = { waiting: "neutral", production: "info", ready: "info", accepted: "warning", packed: "warning", to_ship: "info", shipped: "success" };
const deciding = (row: DataRow) => !row.shipment && (row.stage === "ready" || row.stage === "accepted");

export interface PositionsProps {
  shipments: Shipment[];
  intake: IntakeRow[];
  addressees: ProjectAddressee[];
  itemAddressees: Record<string, ItemAddressee[]>;
  forecasts: Record<string, ProjectForecast>;
  canEdit: boolean;
  /** Открыть отправку или данные проекта этой строки (окно доски). */
  onOpen: (row: DataRow) => void;
  /** «Изменить» у строки проекта без отправки: доставка этой КБ. */
  onEditDelivery: (project: string, items: ItemAddressee[]) => void;
  close: () => void;
}

export function IntakePositionsWindow({ shipments, intake, addressees, itemAddressees, forecasts, canEdit, onOpen, onEditDelivery, close }: PositionsProps) {
  const [onlyGaps, setOnlyGaps] = useState(false);
  const [error, setError] = useState("");
  const canPrint = useCanPrint();
  const cards = useMemo(() => new Map(addressees.map((card) => [card.project, card])), [addressees]);
  const allRows = useMemo(() => dataRows(shipments, intake), [shipments, intake]);
  const intakeByKb = useMemo(() => new Map(intake.map((item) => [kbKey(item), item])), [intake]);
  const sequence = useMemo(() => shipmentSequence(allRows), [allRows]);
  const sourceOf = (row: DataRow) => intakeByKb.get(kbKey(row));
  const itemOf = (row: DataRow) => itemAddressees[row.project]?.find((item) => item.item_index === row.item_index);
  const text = (value: string | null | undefined) => value?.trim() && !/^[\s\-‐-―−_.]*$/.test(value) ? value.trim() : "";
  const contact = (value: string | null | undefined) => text(value).toLowerCase() === "самовывоз" ? "" : text(value);
  const deliveryOf = (row: DataRow) => {
    const data = row.shipment ?? { ...cards.get(row.project), ...itemOf(row)?.effective };
    return { ...data, receiver: contact(data.receiver), phone: contact(data.phone), shipping_point: contact(data.shipping_point) };
  };
  const pickupOf = (row: DataRow) => isPickup(deliveryOf(row).carrier ?? "") || deliveryOf(row).terms === "САМОВЫВОЗ";
  const gapsOf = (row: DataRow) => {
    if (row.shipment) return dataGaps(row.shipment);
    if (!itemOf(row)) return stageGaps(row.stage, cards.get(row.project)?.dispatch_gaps);
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
  const hasGaps = (row: DataRow) => row.shipment ? dataGaps(row.shipment).length > 0
    : gapsOf(row) === undefined || Boolean(gapsOf(row)?.length) || (deciding(row) && !(num(sourceOf(row)?.pack_permission_remaining) > 0));
  const rows = useMemo(() => orderShipmentRows(allRows.filter((row) => row.shipment?.status !== "Отменена" && (!onlyGaps || hasGaps(row)))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [allRows, onlyGaps, cards, itemAddressees, intakeByKb]);

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

  const empty = <span className="muted">—</span>;
  const field = (value: string | null | undefined, required = true) => value?.trim() && !/^[\s\-‐-―−_.]*$/.test(value)
    ? value : required ? <span className="tone-warn">не заполнено</span> : empty;
  const updRequired = (row: DataRow) => !EARLY.includes(row.stage);
  const needsUpdDocument = (row: DataRow) => updRequired(row) && deliveryOf(row).upd_type !== "БЕЗ УПД";
  const qty = (row: DataRow) => row.quantity > 0 || row.shipment ? `${formatNumber(row.quantity)}${NBSP}${row.unit}` : "—";
  const deadlineOf = (row: DataRow) => row.shipment?.deadline ?? sourceOf(row)?.deadline ?? "";
  const forecastOf = (row: DataRow) => readiness([row.project], forecasts, deadlineOf(row) || null, row.shipment?.status ?? "Новая");
  const trackGaps = (row: DataRow) => {
    if (!row.shipment && gapsOf(row) === undefined) return "Данные проекта не загружены";
    const gaps = row.shipment ? [...row.shipment.missing_data, ...row.shipment.missing_ship] : gapsOf(row) ?? [];
    // Доставка и документы уже имеют свои колонки; здесь — остальные причины.
    return [...new Set(gaps)].filter((gap) => !/^(условия доставки|способ доставки|организация|получатель|телефон(?: получателя)?|адрес(?: доставки)?|тк|перевозчик|трек(?:-номер)?|(?:вариант|тип|номер|№|дата|файл) упд)$/i.test(gap)).join(", ");
  };
  const link = (row: DataRow) => `${row.project} ${row.item_index}`;
  const ALL: DataColumn<DataRow>[] = [
    { key: "project", title: "Проект", value: (row) => row.project, width: 112 },
    { key: "customer", title: "Заказчик", value: (row) => row.customer, filter: true, width: 160, wrap: true, maxWidth: 200 },
    { key: "kb", title: "КБ", value: (row) => row.item_index, width: 80 },
    { key: "name", title: "Наименование", value: (row) => row.name, grow: true, minWidth: 180, maxWidth: 280, wrap: true,
      render: (row) => <span title={row.name}>{row.name}</span> },
    { key: "status", title: "Этап позиции", value: rowStageLabel, filter: true, width: 112,
      render: (row) => <StatusChip tone={row.stage ? STAGE_TONE[row.stage] : "neutral"}>{rowStageLabel(row)}</StatusChip> },
    { key: "quantity", title: "Количество", value: (row) => row.quantity, numeric: true, width: 96, render: qty },
    { key: "term", title: "Срок", value: deadlineOf, width: 96, render: (row) => formatDay(deadlineOf(row)) || "—" },
    { key: "forecast", title: "Прогноз готовности", value: (row) => forecastOf(row)?.ready, width: 128,
      render: (row) => { const forecast = forecastOf(row);
        return forecast ? <span className={forecast.late ? "tone-danger" : undefined}>{formatDay(forecast.ready)}</span> : null; } },
    { key: "terms", title: "Способ доставки", value: (row) => pickupOf(row) ? "Самовывоз" : termsText(deliveryOf(row).terms), width: 136,
      render: (row) => field(pickupOf(row) ? "Самовывоз" : termsText(deliveryOf(row).terms)) },
    { key: "carrier", title: "ТК", value: (row) => deliveryOf(row).carrier, width: 104, render: (row) => pickupOf(row) ? empty : field(deliveryOf(row).carrier) },
    { key: "track", title: "Трек", value: (row) => row.shipment?.track_number, width: 128,
      render: (row) => field(row.shipment?.track_number, Boolean(row.shipment && needsTrack(row.shipment))) },
    { key: "receiver", title: "Организация", value: (row) => deliveryOf(row).receiver, width: 160, wrap: true, maxWidth: 220, render: (row) => field(deliveryOf(row).receiver) },
    { key: "contact", title: "Получатель", value: (row) => deliveryOf(row).contact, width: 160, wrap: true, maxWidth: 220, render: (row) => field(deliveryOf(row).contact, false) },
    { key: "phone", title: "Телефон", value: (row) => deliveryOf(row).phone, width: 136, render: (row) => field(deliveryOf(row).phone) },
    { key: "address", title: "Адрес", value: (row) => deliveryOf(row).shipping_point, width: 200, wrap: true, maxWidth: 280,
      render: (row) => pickupOf(row) ? empty : field(deliveryOf(row).shipping_point) },
    { key: "upd", title: "УПД", value: (row) => updLabel(deliveryOf(row).upd_type), filter: true, width: 96, render: (row) => field(updLabel(deliveryOf(row).upd_type), updRequired(row)) },
    { key: "updNumber", title: "№ УПД", value: (row) => deliveryOf(row).upd_number, width: 104, render: (row) => needsUpdDocument(row) ? field(deliveryOf(row).upd_number) : empty },
    { key: "updDate", title: "Дата УПД", value: (row) => deliveryOf(row).upd_date, width: 104, render: (row) => formatDay(deliveryOf(row).upd_date) || "—" },
    { key: "updFile", title: "Файл УПД", value: (row) => deliveryOf(row).upd_url, width: 104,
      render: (row) => deliveryOf(row).upd_url ? <a href={deliveryOf(row).upd_url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>файл</a>
        : needsUpdDocument(row) ? <span className="tone-warn">без файла</span> : empty },
    { key: "pl", title: "PL", value: (row) => row.shipment?.pl_number, width: 128, render: (row) => documentCell(row.shipment, "pl") },
    { key: "dn", title: "DN", value: (row) => row.shipment?.dn_number, width: 128, render: (row) => documentCell(row.shipment, "dn") },
    { key: "sequence", title: "Отгрузка", value: (row) => sequence.get(shipmentGroupKey(row)) ?? "", width: 88 },
    { key: "shipmentStatus", title: "Статус отправки", value: (row) => row.shipment?.status ?? "Не создана", filter: true, width: 120 },
    { key: "ordered", title: "Заказано", value: (row) => num(sourceOf(row)?.needed) || row.quantity, numeric: true, width: 88,
      render: (row) => `${formatNumber(num(sourceOf(row)?.needed) || row.quantity)}${NBSP}${row.unit}` },
    { key: "launched", title: "Запущено", value: (row) => num(sourceOf(row)?.launched), numeric: true, width: 88,
      render: (row) => `${formatNumber(num(sourceOf(row)?.launched))}${NBSP}${row.unit}` },
    { key: "rest", title: "Остаток", hint: "Ещё не принято склада", value: (row) => num(sourceOf(row)?.remaining), numeric: true, width: 88,
      render: (row) => `${formatNumber(num(sourceOf(row)?.remaining))}${NBSP}${row.unit}` },
    { key: "grn", title: "ГРН", value: (row) => grnText(row, sourceOf(row), shipments), width: 144, wrap: true, maxWidth: 200 },
    { key: "createdAt", title: "Создана", value: (row) => row.shipment?.created_at, width: 104, render: (row) => formatDay(row.shipment?.created_at) || "—" },
    { key: "shippedAt", title: "Отгружена", value: (row) => row.shipment?.shipped_at, width: 104, render: (row) => formatDay(row.shipment?.shipped_at) || "—" },
    { key: "trackGaps", title: "Ждём для трека", value: trackGaps, width: 200, wrap: true, maxWidth: 280,
      render: (row) => trackGaps(row) ? <span className="tone-warn">{trackGaps(row)}</span> : null },
    { key: "action", title: "Действие", value: () => "", width: 104,
      render: (row) => <button type="button" className="secondary small" aria-label={`Открыть ${link(row)}`}
        onClick={(event) => {
          event.stopPropagation();
          // Строка без отправки показывает доставку этой КБ — «Изменить» правит её, а не карточку проекта.
          const item = !row.shipment && canEdit ? itemOf(row) : undefined;
          if (item) onEditDelivery(row.project, [item]); else onOpen(row);
        }}>{canEdit ? "Изменить" : "Открыть"}</button> },
  ];
  const HIDDEN = new Set(["contact", "updDate", "updFile", "sequence", "shipmentStatus", "ordered", "launched", "rest", "grn", "createdAt", "shippedAt", "trackGaps"]);
  const columns = ALL.map((column) => ({ ...column, hidden: HIDDEN.has(column.key), export: column.key !== "action",
    render: column.render ?? ((row: DataRow) => cellContent(column, column.value(row))) }));

  return <PageWindow label="Все позиции" close={close} width="min(1760px, 100%)" dismissable>
    <section className="panel intake-journal intake-positions">
      <header className="page-head"><h2>Все позиции</h2>
        <label className="inline-check"><input type="checkbox" checked={onlyGaps} onChange={(event) => setOnlyGaps(event.target.checked)} /> Только с пробелами</label>
        <button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {shipments.length >= 200 && <p className="muted">Загружены последние 200 отправок</p>}
      <DataTable label="Все позиции" storageKey={STORAGE_KEY} rows={rows} columns={columns} rowKey={(row) => row.id}
        rowLabel={link} onRowClick={onOpen} exportName="Все позиции"
        searchPlaceholder="Проект, КБ, наименование, ГРН, организация, получатель, УПД, PL или трек"
        searchText={(row) => row.shipment ? shipmentSearch({ ...row.shipment,
          lines: row.shipment.lines.filter((line) => row.id === `${row.shipment!.id}:${line.id}`),
        }) : Object.values(deliveryOf(row)).filter((value): value is string => typeof value === "string")}
        emptyText={onlyGaps ? "Позиций с пробелами нет." : "Позиций нет."} />
    </section>
  </PageWindow>;
}
