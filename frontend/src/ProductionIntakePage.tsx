import { useEffect, useMemo, useRef, useState } from "react";
import { failureText, shippingApi, type Principal, type ProjectAddressee } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { DispatchCard } from "./DispatchCard";
import { DISPATCH_STAGES, stageLabel, type DispatchStage } from "./dispatchStage";
import { formatDay, formatNumber, NBSP } from "./format";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";
import { actShares, dataRows } from "./dispatchPositionRows";
import { defaultTab, dispatchBoard, hasWork, kbKey, num, ofProject, permittedPack, recentShipment, round2, SHIPPED_DAYS, stagePieces, type Tab } from "./intakeStages";
import { isPickup, termsText } from "./carriers";
import { canAct, canSee, hasFullAccess, urlParam } from "./navigation";
import { intakeApi, type DispatchShipment, type IntakeAct, type IntakeRow } from "./productionIntakeApi";
import { IntakeWindow, keyOf, leftOf } from "./IntakeWindow";
import { PrintOutcome, type PrintResult } from "./LabelPrint";
import { PackWindow } from "./PackWindow";
import { PrintSetupButton } from "./PrintSetupWindow";
import { needsTrack, NoUpdWindow, onlyUpdBlocks, readyForTrack, readyToShip, type CardRights } from "./ShipmentCard";
import { CreateShipmentWindow, JournalWindow, RegisterGoodWindow } from "./ShipmentJournal";
import { updState } from "./ShipmentDocs";
import { ShipWindow } from "./ShipWindow";
import { TrackWindow } from "./TrackWindow";
import { PackingPermissionWindow } from "./PackingPermissionWindow";
import { ShipmentDataWindow } from "./ShipmentData";
import { orderShipmentRows, shipmentGroupKey, shipmentPositions, ShipmentGroupHeader, ProjectShippingData } from "./ShipmentGroupHeader";
import "./productionIntake.css";
import "./shipping.css";

/* «Приёмка с производства» (владелец 14.10, 29.10.2026): всё, что в работе, по проектам — и приёмка, и упаковка, и
   отгрузка на одном экране. Вкладки — шаги пути готовой продукции; КБ, у которой штуки сразу в нескольких шагах, видна в
   каждом. У каждой вкладки свои колонки и одно своё действие (06.11), и кнопка называет штуки своей строки:
   «В производстве» — «Принять досрочно» (предупреждение, не запрет), «Готово» — «Принять», «Принято» — строка на акт
   приёмки с ГРН и «Упаковать», «Упаковано» — «Трек…» (или «Без УПД…», когда УПД нет совсем), «На отгрузку» — «Отгрузить».
   Короба, PL/DN и этикетки — в окне упаковки и отгрузки. Данные отправки вносят менеджер и склад. */

const TABS: ReadonlyArray<readonly [Tab, string]> = [...DISPATCH_STAGES, ["all", "Все"]];
/** Старые ссылки `?section=shipping&tab=…` и вкладки бывшей «Отгрузки» ведут в свои шаги. */
const TAB_ALIASES: Record<string, Tab> = { stock: "accepted", pack: "accepted", shipments: "packed", ship: "to_ship", done: "shipped", production: "production" };
const DELIVERY_COLUMNS = ["receiver", "delivery", "carrier", "address", "phone", "warehouseNote"] as const;
type DeliveryField = typeof DELIVERY_COLUMNS[number];
const DELIVERY_TITLES: Record<DeliveryField, string> = { receiver: "Получатель", delivery: "Способ доставки", carrier: "Перевозчик", address: "Адрес", phone: "Телефон", warehouseNote: "Комментарий для склада" };
const STAGE_TONE: Record<DispatchStage, Tone> = { waiting: "neutral", production: "info", ready: "info", accepted: "warning", packed: "warning", to_ship: "info", shipped: "success" };
/** Колонки вкладки: `shown` — видны сразу, `more` — в «Колонках» (общие данные отправки — в заголовке). «Проект» — скрытая
 *  колонка поиска и группировки. Владелец 29.10: «зачем лишняя инфа, кнопки 2»; аудит 06.11 — колонки снова свои. */
const TAB_COLUMNS: Record<Tab, { shown: string[]; more: string[] }> = {
  waiting: { shown: ["kb", "name", "needed"], more: [...DELIVERY_COLUMNS, "upd"] },
  production: { shown: ["kb", "name", "needed", "launched", "left", "tasks", "action"], more: [...DELIVERY_COLUMNS, "upd"] },
  ready: { shown: ["kb", "name", "needed", "tasks", "action"], more: ["launched", "left", ...DELIVERY_COLUMNS, "upd"] },
  accepted: { shown: ["kb", "name", "grn", "received", "quantity", "action"], more: ["needed", "shipment", "upd", ...DELIVERY_COLUMNS] },
  packed: { shown: ["kb", "name", "quantity", "grn", "action"], more: ["shipment", "track", "shippedAt", "docs", "upd", ...DELIVERY_COLUMNS] },
  to_ship: { shown: ["kb", "name", "quantity", "grn", "action"], more: ["shipment", "track", "shippedAt", "docs", "upd", ...DELIVERY_COLUMNS] },
  shipped: { shown: ["kb", "name", "quantity", "grn"], more: ["shipment", "track", "shippedAt", "docs", "upd", ...DELIVERY_COLUMNS] },
  all: { shown: ["kb", "name", "stage", "quantity", "needed", "launched", "left", "grn", "received", "action"],
    more: ["shipment", "track", "shippedAt", "tasks", "docs", "upd", ...DELIVERY_COLUMNS] },
};
const ACTION_TITLE: Partial<Record<Tab, string>> = { production: "Принять досрочно", ready: "Можно принять", accepted: "Упаковать", to_ship: "Отгрузка", all: "Действие" };
const QUANTITY_TITLE: Partial<Record<Tab, string>> = { packed: "Упаковано", to_ship: "Упаковано", shipped: "Отгружено" };
const EMPTY: Record<Tab, string> = {
  waiting: "В ожидании производства ничего нет.", production: "В производстве ничего нет.", ready: "Готового к приёмке нет.",
  accepted: "Принятого и не упакованного нет.", packed: "Упакованного, но не готового к отгрузке нет.",
  to_ship: "Готового к отгрузке нет — нужны УПД, ТК и трек.", shipped: `Отгруженного за ${SHIPPED_DAYS} дней нет — история в «Журнале».`, all: "Нет КБ в работе",
};
const DOCS_HINT = "PL — упаковочный лист, DN — отгрузочная ведомость; печать — в окне упаковки и отгрузки";

/** Строка таблицы: часть КБ в одном шаге — строка отправки, акт приёмки (остаток по ГРН) или остаток шага. */
interface IntakeLine {
  id: string; project: string; item_index: string; name: string; unit: string;
  stage: DispatchStage | null; quantity: number; kb: IntakeRow; shipment?: DispatchShipment; act?: IntakeAct; grns: string[];
  /** «Принято», остаток на складе: сколько из этой строки можно упаковать по разрешению КБ. */
  packNow: number;
}

const dash = <span className="muted">—</span>;
const tasksText = (row: IntakeRow) => row.tasks.map((task) => `${task.number}${task.site ? ` · ${task.site}` : ""} · сдано ${task.delivered} из ${task.quantity}`).join("\n");
const who = (row: Pick<IntakeRow, "project" | "item_index">) => `${row.project} ${row.item_index}`;

function urlTab(): { tab: Tab | null; journal: boolean } {
  const wanted = new URLSearchParams(window.location.search).get("tab") ?? "";
  if (wanted === "journal") return { tab: null, journal: true };
  const tab = TAB_ALIASES[wanted] ?? wanted;
  return { tab: TABS.some(([key]) => key === tab) ? tab as Tab : null, journal: false };
}

type Opened = { kind: "pack" | "ship"; project: string } | { kind: "track"; title: string; shipment: DispatchShipment } | { kind: "noupd"; shipment: DispatchShipment };
type Accepting = { rows: IntakeRow[]; quantities: Record<string, number> };

export function ProductionIntakePage({ principal }: { principal: Principal }) {
  // Принимает, упаковывает и отгружает кладовщик («Отгрузка — склад»); менеджер («Отгрузка») — данные отправки, заявки.
  const canWarehouse = canAct(principal, "shipping_goods");
  const canManage = canAct(principal, "shipping");
  const canDispatch = canManage || canWarehouse;
  const rights: CardRights = { canManage, canWarehouse, canEdit: canDispatch, canUnship: hasFullAccess(principal) };
  const fromUrl = useRef(urlTab());
  const [rows, setRows] = useState<IntakeRow[]>([]);
  const [shipments, setShipments] = useState<DispatchShipment[]>([]);
  const [addressees, setAddressees] = useState<ProjectAddressee[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [tab, setTab] = useState<Tab>(fromUrl.current.tab ?? "ready");
  const tabChosen = useRef(Boolean(fromUrl.current.tab));
  const [accepting, setAccepting] = useState<Accepting | null>(null);
  const [opened, setOpened] = useState<Opened | null>(null);
  const [header, setHeader] = useState<"journal" | "good" | "request" | null>(fromUrl.current.journal ? "journal" : null);
  const [label, setLabel] = useState<{ grn: string; result: PrintResult | null; error: string; busy: boolean } | null>(null);
  const [projectData, setProjectData] = useState<string | null>(null);
  const [permissionProject, setPermissionProject] = useState<string | null>(null);
  const [shipmentData, setShipmentData] = useState<string | null>(null);

  async function reload() {
    try {
      const [intakeResult, shipmentResult, addresseeResult] = await Promise.all([intakeApi.rows(), intakeApi.shipments(), shippingApi.addressees()]);
      const loadedRows = intakeResult.rows ?? [];
      const loadedShipments = shipmentResult.shipments ?? [];
      const linkedShipment = urlParam("shipment");
      if (linkedShipment && !loadedShipments.some((shipment) => shipment.id === linkedShipment)) {
        loadedShipments.push(...(await intakeApi.shipments(linkedShipment)).shipments);
      }
      setRows(loadedRows); setShipments(loadedShipments); setError("");
      setAddressees(addresseeResult.addressees ?? []);
      if (!tabChosen.current) {
        const board = dispatchBoard(loadedRows, loadedShipments);
        setTab(defaultTab(board.rows, (row) => board.pieces.get(kbKey(row))!, canWarehouse));
      }
    } catch (reason) {
      setError(failureText(reason, "Не удалось загрузить список КБ в работе"));
    } finally { setLoaded(true); }
  }
  useEffect(() => { void reload(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Счётчики вкладок — общие с «Данными отправок» (`dispatchBoard`), строки — общие позиции (`dataRows`).
  const board = useMemo(() => dispatchBoard(rows, shipments), [rows, shipments]);
  const piecesOf = (row: IntakeRow) => board.pieces.get(kbKey(row)) ?? stagePieces(row, shipments);
  const lines = useMemo(() => {
    const byKb = new Map(board.rows.map((row) => [kbKey(row), row]));
    return orderShipmentRows(dataRows(shipments, rows)
      .filter((position) => tab === "all" || position.stage === tab && (tab !== "shipped" || Boolean(position.shipment && recentShipment(position.shipment))))
      .flatMap((position): IntakeLine[] => {
        const kb = byKb.get(kbKey(position))!;
        const base = { project: position.project, item_index: position.item_index, name: position.name, unit: position.unit, stage: position.stage, kb, packNow: 0 };
        if (position.shipment) return [{ ...base, id: position.id, quantity: position.quantity, shipment: position.shipment, grns: position.grns }];
        if (position.stage !== "accepted") return [{ ...base, id: position.id, quantity: position.quantity, grns: position.grns }];
        // «Принято»: строка на каждый акт приёмки со своим ГРН; разрешение КБ делится по строкам — старое первым.
        let allowed = permittedPack(kb);
        return actShares(kb, shipments).map((share, index) => {
          const packNow = round2(Math.min(share.quantity, allowed));
          allowed = round2(allowed - packNow);
          return { ...base, id: `${position.id}:${share.act?.grn ?? index}`, quantity: share.quantity, act: share.act ?? undefined, grns: share.grn ? [share.grn] : [], packNow };
        });
      }));
  }, [board, rows, shipments, tab]);
  const head = useMemo(() => {
    const byProject: Record<string, IntakeRow> = {};
    for (const row of board.rows) if (!byProject[row.project]) byProject[row.project] = row;
    return byProject;
  }, [board]);
  const addresseeByProject = useMemo(() => new Map(addressees.map((card) => [card.project.trim().toUpperCase(), card])), [addressees]);
  const cardOf = (row: Pick<IntakeRow, "project">) => addresseeByProject.get(row.project.trim().toUpperCase());

  /* Строки отправки показывают её данные, остальные — карточку проекта. */
  const deliveryOf = (line: IntakeLine): Array<ProjectAddressee | DispatchShipment> => line.shipment ? [line.shipment] : [cardOf(line)].filter((card): card is ProjectAddressee => Boolean(card));
  const contactText = (line: IntakeLine, value: string) => line.shipment && value.trim().toLowerCase() === "самовывоз" ? "" : value;
  const deliveryText = (line: IntakeLine, field: DeliveryField, data: ProjectAddressee | DispatchShipment): string => {
    if (field === "receiver") return [...new Set([data.receiver, data.contact].map((value) => contactText(line, value)).filter(Boolean))].join(" · ");
    if (field === "delivery") return isPickup(data.carrier) ? "Самовывоз" : termsText(data.terms);
    if (field === "carrier") return data.carrier;
    if (field === "address") return line.shipment ? contactText(line, data.shipping_point) : isPickup(data.carrier) ? "Не требуется" : data.shipping_point;
    if (field === "phone") return contactText(line, data.phone);
    return [...new Set([line.shipment ? "" : cardOf(line)?.note, data.note, "tk_note" in data ? data.tk_note : ""].map((text) => text?.trim()).filter(Boolean))].join("\n");
  };
  const deliveryCell = (line: IntakeLine, field: DeliveryField) => {
    const data = deliveryOf(line)[0];
    if (!data) return dash;
    const text = deliveryText(line, field, data);
    if (field === "warehouseNote") return text ? <span className="intake-delivery-list">{text.split("\n").map((note, index) => <span className="intake-note-line" key={index}>{note}</span>)}</span> : dash;
    return text ? <span>{text}</span> : dash;
  };
  const updText = (line: IntakeLine) => {
    if (line.shipment) return updState(line.shipment).text;
    const card = cardOf(line);
    if (!card) return "";
    return card.upd_type === "БЕЗ УПД" ? "Без УПД" : card.upd_number ? `УПД № ${card.upd_number}` : "УПД не внесён";
  };
  const trackText = (shipment: DispatchShipment | undefined, line: IntakeLine) =>
    !shipment ? "" : contactText(line, shipment.track_number) || (!needsTrack(shipment) ? "Не требуется" : "");

  /** «Печать» этикетки ГРН акта: та же очередь печати, что у приёмки закупки; статус — под таблицей. */
  async function printLabel(grn: string) {
    if (label?.busy) return;
    setLabel({ grn, result: null, error: "", busy: true });
    try { setLabel({ grn, result: await intakeApi.printLabel(grn), error: "", busy: false }); }
    catch (reason) { setLabel({ grn, result: null, error: failureText(reason, "Этикетка не напечатана"), busy: false }); }
  }
  const mutate = async (key: string, operation: () => Promise<unknown>, message: string) => {
    if (busy) return false;
    setBusy(key); setError(""); setNotice("");
    try { await operation(); setNotice(message); await reload(); return true; }
    catch (reason) { setError(failureText(reason, "Операция не выполнена")); return false; }
    finally { setBusy(""); }
  };
  async function accepted(pieceCount: number) {
    setAccepting(null); setError("");
    setNotice(`Принято на склад: ${pieceCount} шт`);
    await reload();
  }
  const accept = (kbs: IntakeRow[], quantity: (row: IntakeRow) => number) =>
    setAccepting({ rows: kbs, quantities: Object.fromEntries(kbs.map((row) => [keyOf(row), quantity(row)])) });

  /** «Принять досрочно» из «В производстве»: сколько можно принять сверх уже сданного цехом. */
  const earlyQuantity = (line: IntakeLine) => round2(Math.min(line.quantity, Math.max(num(line.kb.remaining) - piecesOf(line.kb).ready, 0)));
  /** «Трек…» упакованной отправки строки: одно действие — «Трек…», «Без УПД…» (УПД нет совсем) или выключенный «Трек…» с причиной. */
  const trackAction = (line: IntakeLine) => {
    const shipment = line.shipment;
    if (!shipment || shipment.status !== "Упакована" || !canDispatch) return null;
    const needing = needsTrack(shipment);
    const ready = needing && (readyForTrack(shipment) || Boolean(shipment.track_number));
    if (!ready && canWarehouse && onlyUpdBlocks(shipment)) return <button type="button" className="secondary small intake-print" title="УПД нет — отгрузку без УПД разрешает уполномоченный по ПИН"
      aria-label={`Без УПД ${who(line)}`} onClick={() => setOpened({ kind: "noupd", shipment })}>Без УПД…</button>;
    if (!needing) return null;
    const gaps = shipment.missing_ship.filter((gap) => gap !== "трек-номер");
    const button = <button type="button" className="secondary small intake-print" disabled={!ready}
      title={ready ? "Вписать трек-номер ТК" : `Трек вносится, когда есть УПД и ТК${gaps.length ? ` — не хватает: ${gaps.join(", ")}` : ""}. Ждём данные менеджера`}
      aria-label={`Трек ${who(line)}`} onClick={() => setOpened({ kind: "track", title: who(line), shipment })}>Трек…</button>;
    // Почему выключено — видно сразу, не только в подсказке.
    return ready || !gaps.length ? button : <span className="intake-track-cell">{button}<small className="muted">ждём: {gaps.join(", ")}</small></span>;
  };
  /** Одно действие строки — по её шагу; число на кнопке — штуки этой строки. */
  const actionCell = (line: IntakeLine) => {
    if (line.stage === "production") {
      const quantity = earlyQuantity(line);
      if (!canWarehouse || !(quantity > 0)) return dash;
      return <button type="button" className="secondary small intake-early" title="Ещё в производстве — принять досрочно"
        aria-label={`Принять досрочно ${who(line)}: ${quantity}`} onClick={() => accept([line.kb], () => quantity)}>Принять досрочно · {formatNumber(quantity)}</button>;
    }
    if (line.stage === "ready") {
      if (!canWarehouse) return <strong className="tone-ok" title={tasksText(line.kb)}>{formatNumber(line.quantity)}</strong>;
      return <button type="button" className="intake-take-btn" title={tasksText(line.kb) || "Принять на склад"} aria-label={`Принять ${who(line)}: ${line.quantity}`}
        onClick={() => accept([line.kb], () => line.quantity)}>Принять · {formatNumber(line.quantity)}</button>;
    }
    if (line.stage === "accepted") {
      if (!canDispatch) return dash;
      if (line.shipment) return <button type="button" className="intake-take-btn" title={`Набрано в ${line.shipment.number}: короба и PL/DN — в окне упаковки`}
        aria-label={`Упаковать ${who(line)}: ${line.quantity}`} onClick={() => setOpened({ kind: "pack", project: line.project })}>Упаковать · {formatNumber(line.quantity)}</button>;
      if (line.packNow > 0) return <button type="button" className="intake-take-btn" title={`Разрешено упаковать по КБ: ${permittedPack(line.kb)}`}
        aria-label={`Упаковать ${who(line)}: ${line.packNow}`} onClick={() => setOpened({ kind: "pack", project: line.project })}>Упаковать · {formatNumber(line.packNow)}</button>;
      return <span className="intake-permission-cell"><span className="tone-warn">Нужно разрешение</span>
        <small>разрешено {formatNumber(num(line.kb.pack_permission_quantity))} · упаковано {formatNumber(num(line.kb.packed))} · на складе {formatNumber(line.quantity)}</small>
        {canManage && <button type="button" className="secondary small" aria-label={`Разрешить упаковку ${line.project} ${line.item_index}`}
          onClick={() => setPermissionProject(line.project)}>Разрешить…</button>}
      </span>;
    }
    if (line.stage === "packed") return trackAction(line) ?? dash;
    if (line.stage === "to_ship" && line.shipment && canDispatch && readyToShip(line.shipment)) return <button type="button" className="primary small intake-print"
      title={`Отгрузить ${line.shipment.number}`} aria-label={`Отгрузить ${who(line)}`} onClick={() => setOpened({ kind: "ship", project: line.project })}>Отгрузить</button>;
    return dash;
  };

  const columns: DataColumn<IntakeLine>[] = [
    { key: "project", title: "Проект", value: (line) => line.project, hidden: true },
    { key: "kb", title: "КБ", width: 56, maxWidth: 72, value: (line) => line.item_index, render: (line) => <span className="intake-kb">{line.item_index}</span> },
    // Строка — одна линия (владелец 15.10): длинное наименование обрезается «…», целиком — в подсказке.
    { key: "name", title: "Наименование", grow: true, minWidth: 160, wrap: true, value: (line) => line.name, render: (line) => <span className="ship-position-name" title={line.name}>{line.name}</span> },
    { key: "stage", title: "Этап", width: 112, value: (line) => line.stage ? stageLabel(line.stage) : line.shipment?.status === "Отменена" ? "Отменена" : "Не набрано",
      render: (line) => <StatusChip tone={line.stage ? STAGE_TONE[line.stage] : "neutral"}>{line.stage ? stageLabel(line.stage) : line.shipment?.status === "Отменена" ? "Отменена" : "Не набрано"}</StatusChip> },
    { key: "quantity", title: QUANTITY_TITLE[tab] ?? "Количество", width: 88, numeric: true, value: (line) => line.quantity,
      render: (line) => `${formatNumber(line.quantity)}${NBSP}${line.unit}` },
    { key: "needed", title: "Заказано", numeric: true, value: (line) => num(line.kb.needed), render: (line) => num(line.kb.needed) > 0 ? formatNumber(num(line.kb.needed)) : dash },
    { key: "launched", title: "Запущено", numeric: true, value: (line) => num(line.kb.launched), render: (line) => num(line.kb.launched) > 0 ? formatNumber(num(line.kb.launched)) : dash },
    { key: "left", title: "Остаток", hint: "Заказано − принято по всем ГРН", numeric: true, value: (line) => leftOf(line.kb), render: (line) => leftOf(line.kb) > 0 ? formatNumber(leftOf(line.kb)) : dash },
    // «Задания» видны в «В производстве» и «Готово» (что цех сдал и сколько); в остальных вкладках — в «Колонках» у «Все».
    { key: "tasks", title: "Задания", wrap: true, maxWidth: 200, value: (line) => line.kb.tasks.map((task) => task.number).join(", "),
      render: (line) => line.kb.tasks.length ? <span className="intake-clip intake-stage muted" title={tasksText(line.kb)}>{line.kb.tasks.map((task) => task.number).join(", ")}</span> : dash },
    { key: "grn", title: tab === "shipped" ? "ГРН партии" : "ГРН", wrap: true, width: 128, maxWidth: 200, value: (line) => line.grns.join(", "), render: (line) => {
      if (!line.grns.length) return dash;
      const printable = canWarehouse && line.stage === "accepted" && !line.shipment;
      return <span className="intake-grn">{line.grns.map((grn) => <span className="intake-grn-line" key={grn}><span className="goods-grn">{grn}</span>
        {printable && <button type="button" className="link-button intake-grn-print" title="Печать этикетки ГРН" aria-label={`Печать этикетки ${grn}`}
          disabled={Boolean(label?.busy)} aria-busy={label?.busy && label.grn === grn} onClick={() => void printLabel(grn)}>Печать</button>}</span>)}</span>;
    } },
    { key: "received", title: "Дата приёмки", width: 96, value: (line) => line.act?.received_at ?? "", render: (line) => line.act ? formatDay(line.act.received_at) : dash },
    { key: "shipment", title: "Отправка", width: 130, maxWidth: 160, value: (line) => line.shipment ? `${line.shipment.number} ${line.shipment.status}` : "",
      render: (line) => line.shipment ? <span title={line.shipment.status}>{line.shipment.number}</span> : dash },
    { key: "track", title: "Трек", wrap: true, minWidth: 84, maxWidth: 180, value: (line) => trackText(line.shipment, line), render: (line) => {
      const text = trackText(line.shipment, line);
      const action = tab === "packed" ? trackAction(line) : null;
      return action ? <span className="intake-track-cell">{text && <span>{text}</span>}{action}</span> : text ? <span>{text}</span> : dash;
    } },
    { key: "shippedAt", title: "Дата отгрузки", width: 104, value: (line) => line.shipment?.shipped_at ?? "",
      render: (line) => line.shipment?.shipped_at ? formatDay(line.shipment.shipped_at) : dash },
    { key: "docs", title: "PL/DN", hint: DOCS_HINT, wrap: true, maxWidth: 200, value: (line) => line.shipment ? [line.shipment.pl_number, line.shipment.dn_number].filter(Boolean).join(" · ") : "",
      render: (line) => !line.shipment ? dash : line.shipment.pl_number || line.shipment.dn_number
        ? <span title={DOCS_HINT}>{[line.shipment.pl_number, line.shipment.dn_number].filter(Boolean).join(" · ")}</span> : <span className="muted">не сформированы</span> },
    { key: "upd", title: "УПД", wrap: true, maxWidth: 180, value: updText, render: (line) => {
      const text = updText(line);
      const href = line.shipment ? updState(line.shipment).href : cardOf(line)?.upd_url ?? "";
      return !text ? dash : href ? <a href={href} target="_blank" rel="noreferrer">{text}</a> : <span>{text}</span>;
    } },
    ...DELIVERY_COLUMNS.map((key): DataColumn<IntakeLine> => ({ key, title: DELIVERY_TITLES[key],
      value: (line) => deliveryOf(line).map((data) => deliveryText(line, key, data)).join("; "),
      wrap: true, minWidth: key === "address" || key === "warehouseNote" ? 120 : 84, maxWidth: key === "address" || key === "warehouseNote" ? 220 : 160,
      render: (line) => deliveryCell(line, key) })),
    { key: "action", title: ACTION_TITLE[tab] ?? "Действие", value: (line) => line.stage === "accepted" ? line.packNow || (line.shipment ? line.quantity : 0) : line.stage === "ready" ? line.quantity : "", render: actionCell },
  ];
  // Без права на действие колонка действия была бы из одних прочерков — её нет («Можно принять» показывает число).
  const actionless = (tab === "production" && !canWarehouse) || (["accepted", "to_ship"].includes(tab) && !canDispatch);
  const shown = TAB_COLUMNS[tab].shown.filter((key) => key !== "action" || !actionless);
  const { more } = TAB_COLUMNS[tab];
  const visibleColumns = ["project", ...shown, ...more].flatMap((key) => columns.filter((column) => column.key === key))
    .map((column) => ({ ...column, hidden: column.key === "project" || !shown.includes(column.key) }));

  const groupLabel = (_key: string, groupRows: IntakeLine[], filteredRows: IntakeLine[]) => {
    const positions = shipmentPositions(filteredRows);
    const project = groupRows[0].project;
    const shipment = groupRows[0].shipment;
    if (shipment) {
      const position = positions.get(`${project}\u0001${shipment.id}`)!;
      return <ShipmentGroupHeader project={project} customer={head[project]?.customer || shipment.customer} shipment={shipment} {...position}
        quantity={groupRows.reduce((sum, line) => sum + line.quantity, 0)} onError={setError} onOpenShipment={() => setShipmentData(shipment.id)}>
        {tab === "packed" && canDispatch && <button type="button" className="secondary small"
          onClick={(event) => { event.stopPropagation(); setOpened({ kind: "pack", project }); }}>Короба и PL/DN</button>}
      </ShipmentGroupHeader>;
    }
    const title = [project, head[project]?.customer].filter(Boolean).join(" · ");
    // «Принять всё» — только в «Готово» (готовое) и «Все» (всё, что можно принять); КБ — один раз.
    const bulk = tab === "ready" || tab === "all" ? [...new Map(groupRows.filter((line) => tab === "ready" ? line.stage === "ready" : num(line.kb.remaining) > 0)
      .map((line) => [kbKey(line.kb), line.kb])).values()] : [];
    const quantity = (row: IntakeRow) => tab === "ready" ? piecesOf(row).ready : num(row.remaining);
    const total = round2(bulk.reduce((sum, row) => sum + quantity(row), 0));
    return <span className="ship-position-group"><span className="ship-position-group-title">{title}</span>
      <ProjectShippingData data={cardOf(groupRows[0])} />
      <button type="button" className="secondary small" aria-label={`Изменить данные отправки ${project}`}
        onClick={(event) => { event.stopPropagation(); setProjectData(project); }}>Изменить</button>
      {canWarehouse && bulk.length > 1 && <button type="button" className="primary small" onClick={(event) => { event.stopPropagation(); accept(bulk, quantity); }}>
        Принять всё: {bulk.length} КБ · {formatNumber(total)} шт</button>}
    </span>;
  };

  const projectRows = (project: string) => rows.filter((row) => row.project === project);
  const projectShips = (project: string) => shipments.filter((shipment) => ofProject(shipment, project));
  const changeTab = (key: Tab) => { tabChosen.current = true; setTab(key); setNotice(""); };

  return <section className="page intake-page shipping-page ship-positions-page">
    <header className="page-head"><h1>Приёмка с производства</h1>
      <div className="page-actions">
        <button type="button" className="secondary small" onClick={() => setHeader("journal")}>Журнал</button>
        {canManage && <button type="button" className="secondary small" onClick={() => setHeader("request")}>+ Заявка на отгрузку</button>}
        {canManage && <button type="button" className="secondary small" onClick={() => setHeader("good")}>+ Позиция ГП</button>}
        {/* Принтер и шаблон этикеток — складские настройки: видит тот, кому открыт склад, правит — кто его правит. */}
        {canSee(principal, "warehouse") && <PrintSetupButton kind="intake" readOnly={!canAct(principal, "warehouse")} />}
      </div></header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    {!loaded ? <div className="empty">Загрузка…</div> : <>
      <div className="seg-tabs" role="tablist" aria-label="Шаг">{TABS.map(([key, text]) =>
        <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : undefined} onClick={() => changeTab(key)}
          title="Число — КБ в шаге; во «Все» КБ считается один раз">
          {text}<span className="page-count"> {board.counts[key]}</span></button>)}</div>
      <DataTable key={tab} label="Приёмка с производства" storageKey={`production-intake:v4:${tab}`} rows={lines} columns={visibleColumns} rowKey={(line) => line.id}
        groupBy={shipmentGroupKey} groupLabel={groupLabel} groupSort exportName="Приёмка с производства"
        searchText={(line) => [line.shipment?.number]} initialQuery={urlParam("q")} searchPlaceholder="Проект, КБ, наименование, ГРН или задание"
        rowClassName={(line) => tab === "all" && !(line.stage && hasWork(line.stage, line.kb, piecesOf(line.kb))) ? "ship-line-off" : ""}
        emptyText={board.rows.length ? EMPTY[tab] : EMPTY.all} />
      {label && <div className="intake-print-status"><strong>Этикетка {label.grn}</strong><PrintOutcome result={label.result} error={label.error} /></div>}
    </>}
    {accepting && <IntakeWindow rows={accepting.rows} quantities={accepting.quantities} close={() => setAccepting(null)} onDone={accepted} />}
    {opened?.kind === "pack" && <PackWindow project={opened.project} rows={projectRows(opened.project)} shipments={projectShips(opened.project)} rights={rights} reload={reload} close={() => setOpened(null)} />}
    {opened?.kind === "ship" && <ShipWindow project={opened.project} shipments={projectShips(opened.project)} rights={rights} reload={reload} close={() => setOpened(null)} />}
    {opened?.kind === "track" && <TrackWindow title={opened.title} shipments={[shipments.find((shipment) => shipment.id === opened.shipment.id) ?? opened.shipment]} reload={reload} close={() => setOpened(null)} />}
    {opened?.kind === "noupd" && <NoUpdWindow shipment={opened.shipment} mutate={(key, operation, message) => mutate(key, operation, typeof message === "string" ? message : "Готово")} close={() => setOpened(null)} />}
    {header === "journal" && <JournalWindow shipments={shipments} selectedShipment={shipments.find((shipment) => shipment.id === urlParam("shipment"))} initialQuery={urlParam("q")} close={() => setHeader(null)} />}
    {header === "good" && <RegisterGoodWindow reload={reload} close={() => setHeader(null)} />}
    {header === "request" && <CreateShipmentWindow reload={reload} close={() => setHeader(null)} />}
    {permissionProject && <PackingPermissionWindow project={permissionProject} editable={canManage} close={() => setPermissionProject(null)} saved={reload} />}
    {shipmentData && shipments.find((shipment) => shipment.id === shipmentData) && <ShipmentDataWindow
      key={shipmentData} shipment={shipments.find((shipment) => shipment.id === shipmentData)!} canEdit={canDispatch}
      close={() => setShipmentData(null)} onSaved={reload} />}
    {projectData && <DispatchCard project={projectData} customer={head[projectData]?.customer} stage={tab === "all" ? undefined : tab} canEdit={canDispatch} onSaved={() => void reload()} close={() => setProjectData(null)} />}
  </section>;
}
