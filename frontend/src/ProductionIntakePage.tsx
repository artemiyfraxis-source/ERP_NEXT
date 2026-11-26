import { QualityChip } from "./GrnFields";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { failureText, shippingApi, type DeliveryValues, type ItemAddressee, type Principal, type ProjectAddressee } from "./api";
import { cellContent, DataTable, type DataColumn } from "./DataTable";
import { DispatchCard } from "./DispatchCard";
import { ItemDeliveryWindow } from "./ProjectData";
import { DISPATCH_STAGES, stageLabel, type DispatchStage } from "./dispatchStage";
import { formatDay, formatNumber, NBSP, plural } from "./format";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";
import { actShares, dataRows } from "./dispatchPositionRows";
import { defaultTab, dispatchBoard, hasWork, kbKey, num, ofProject, permittedPack, recentShipment, round2, SHIPPED_DAYS, stagePieces, type Tab } from "./intakeStages";
import { isPickup, termsText } from "./carriers";
import { canAct, canSee, hasFullAccess, urlParam } from "./navigation";
import { deliveryGroups, intakeApi, type DispatchShipment, type IntakeAct, type IntakeRow as ApiIntakeRow } from "./productionIntakeApi";
import { IntakeWindow, keyOf, leftOf, RollbackActWindow } from "./IntakeWindow";
import { PrintOutcome, type PrintResult } from "./LabelPrint";
import { PackWindow } from "./PackWindow";
import { PrintSetupButton } from "./PrintSetupWindow";
import { needsTrack, NoUpdWindow, onlyUpdBlocks, readyForTrack, readyToShip, ShipWithoutDataWindow, type CardRights, type Mutate } from "./ShipmentCard";
import { CreateShipmentWindow, JournalWindow, RegisterGoodWindow, useDocLink } from "./ShipmentJournal";
import { ShipWindow } from "./ShipWindow";
import { TrackWindow } from "./TrackWindow";
import { PackingPermissionWindow } from "./PackingPermissionWindow";
import { ShipmentDataWindow, updLabel } from "./ShipmentData";
import { orderShipmentRows, shipmentGroupKey, shipmentSequence } from "./ShipmentGroupHeader";
import { shipmentSearch } from "./shipmentSearch";
import "./productionIntake.css";
import "./shipping.css";

/* «Приёмка с производства» (владелец 14.10, 29.10.2026): всё, что в работе, по проектам — и приёмка, и упаковка, и
   отгрузка на одном экране. Вкладки — шаги пути готовой продукции; КБ, у которой штуки сразу в нескольких шагах, видна в
   каждом. У каждой вкладки свои колонки и одно своё действие (06.11), и кнопка называет штуки своей строки:
   «В производстве» — «Принять досрочно» (предупреждение, не запрет), «Готово» — «Принять», «Принято» — строка на акт
   приёмки с ГРН и «Упаковать», «Упаковано» — «Трек…» и «Без УПД…» в строке самой отправки (КБ-строки без кнопок; трек — только ТК, что его выдаёт),
   «На отгрузку» — «Отгрузить» (отправка без трека — самовывоз, наш курьер, Яндекс Доставка — попадает сюда сама, как только есть УПД).
   Короба, PL/DN и этикетки — в окне упаковки и отгрузки. Данные отправки вносят менеджер и склад. */

const TABS: ReadonlyArray<readonly [Tab, string]> = [...DISPATCH_STAGES, ["all", "Все"]];
/** Старые ссылки `?section=shipping&tab=…` и вкладки бывшей «Отгрузки» ведут в свои шаги. */
const TAB_ALIASES: Record<string, Tab> = { stock: "accepted", pack: "accepted", shipments: "packed", ship: "to_ship", done: "shipped", production: "production" };
const DELIVERY_COLUMNS = ["receiver", "contact", "phone", "address", "delivery", "carrier", "warehouseNote", "carrierNote"] as const;
type DeliveryField = typeof DELIVERY_COLUMNS[number];
const DELIVERY_TITLES: Record<DeliveryField, string> = { receiver: "Организация", phone: "Телефон", address: "Адрес", delivery: "Способ доставки", carrier: "ТК", contact: "Получатель", warehouseNote: "Комментарий для склада", carrierNote: "Для перевозчика" };
const STAGE_TONE: Record<DispatchStage, Tone> = { waiting: "neutral", production: "info", ready: "info", accepted: "warning", packed: "warning", to_ship: "info", shipped: "success" };
/** Row identity, quantities and the action come first; shipping fields stay visible on every tab. */
const TAB_COLUMNS: Record<Tab, string[]> = {
  waiting: ["kb", "name", "needed"],
  production: ["kb", "name", "needed", "launched", "left", "action", "tasks"],
  ready: ["kb", "name", "needed", "action", "tasks"],
  accepted: ["kb", "name", "quantity", "action", "grn", "quality", "received", "label", "packStatus", "packPermission", "packedTotal"],
  packed: ["kb", "name", "quantity", "action", "grn"],
  to_ship: ["kb", "name", "quantity", "action", "grn"],
  shipped: ["kb", "name", "quantity", "grn"],
  all: ["kb", "name", "quantity", "needed", "launched", "left", "action", "stage", "grn", "quality", "received", "label", "tasks", "packStatus", "packPermission", "packedTotal"],
};
const ACTION_TITLE: Partial<Record<Tab, string>> = { production: "Принять досрочно", ready: "Можно принять", accepted: "Упаковать", to_ship: "Отгрузка", all: "Действие" };
const QUANTITY_TITLE: Partial<Record<Tab, string>> = { packed: "Упаковано", to_ship: "Упаковано", shipped: "Отгружено" };
const EMPTY: Record<Tab, string> = {
  waiting: "В ожидании производства ничего нет.", production: "В производстве ничего нет.", ready: "Готового к приёмке нет.",
  accepted: "Принятого и не упакованного нет.", packed: "Упакованного, но не готового к отгрузке нет.",
  to_ship: "Готового к отгрузке нет — нужны УПД, ТК и трек.", shipped: `Отгруженного за ${SHIPPED_DAYS} дней нет — история в «Журнале».`, all: "Нет КБ в работе",
};
type IntakeRow = ApiIntakeRow & { pack_auto_allowed?: boolean };
const AUTO_PACK_LABEL = "Весь заказ принят — разрешение не нужно";

function shippingValue(label: string, value?: string) {
  const text = value?.trim() ?? "";
  const pickupPlaceholder = text.toLowerCase() === "самовывоз" && label !== "ТК" && label !== "Способ доставки";
  const filled = text && !/^[\s\-‐-―−_.]*$/.test(text) && !/^(?:не заполнено|не указан[оа]?)$/i.test(text) && !pickupPlaceholder;
  return <span className={filled ? undefined : "tone-warn"} role="group" aria-label={label} data-label={label}
    title={filled ? text : `${label}: не заполнено`}>{filled ? text : "не заполнено"}</span>;
}

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

type Opened = { kind: "pack" | "ship"; project: string } | { kind: "track"; title: string; shipment: DispatchShipment } | { kind: "noupd" | "bare"; shipment: DispatchShipment };
type Accepting = { rows: IntakeRow[]; quantities: Record<string, number>; blank?: boolean };
type RollingBack = { row: IntakeRow; act: IntakeAct; onStock: number };

export function ProductionIntakePage({ principal }: { principal: Principal }) {
  // Принимает, упаковывает и отгружает кладовщик («Отгрузка — склад»); менеджер («Отгрузка») — данные отправки, заявки.
  const canWarehouse = canAct(principal, "shipping_goods");
  const canManage = canAct(principal, "shipping");
  const canDispatch = canManage || canWarehouse;
  const rights: CardRights = { canManage, canWarehouse, canEdit: canDispatch, canUnship: hasFullAccess(principal) };
  // «Откатить приёмку» — тот же гейт, что у «Откатить отгрузку»: руководитель (администратор).
  const canRollback = hasFullAccess(principal);
  const fromUrl = useRef(urlTab());
  const [rows, setRows] = useState<IntakeRow[]>([]);
  const [shipments, setShipments] = useState<DispatchShipment[]>([]);
  const [addressees, setAddressees] = useState<ProjectAddressee[]>([]);
  const [itemAddressees, setItemAddressees] = useState<Record<string, ItemAddressee[]>>({});
  const [deliveryEdit, setDeliveryEdit] = useState<{ project: string; items: ItemAddressee[] } | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [tab, setTab] = useState<Tab>(fromUrl.current.tab ?? "ready");
  const tabChosen = useRef(Boolean(fromUrl.current.tab));
  const [accepting, setAccepting] = useState<Accepting | null>(null);
  const [rollingBack, setRollingBack] = useState<RollingBack | null>(null);
  const [opened, setOpened] = useState<Opened | null>(null);
  const [header, setHeader] = useState<"journal" | "good" | "request" | null>(fromUrl.current.journal ? "journal" : null);
  const [label, setLabel] = useState<{ grn: string; result: PrintResult | null; error: string; busy: boolean } | null>(null);
  const [projectData, setProjectData] = useState<string | null>(null);
  const [permissionProject, setPermissionProject] = useState<string | null>(null);
  const [shipmentData, setShipmentData] = useState<string | null>(null);
  const { error: docError, link: docLink } = useDocLink();

  async function reload() {
    try {
      const [intakeResult, shipmentResult, addresseeResult] = await Promise.all([intakeApi.rows(), intakeApi.shipments(), shippingApi.addressees()]);
      const loadedRows = intakeResult.rows ?? [];
      const projects = [...new Set(loadedRows.map((row) => row.project))];
      const deliveries: Record<string, ItemAddressee[]> = {};
      await Promise.all(Array.from({ length: Math.min(4, projects.length) }, async () => {
        for (let project = projects.pop(); project !== undefined; project = projects.pop()) {
          deliveries[project] = (await shippingApi.itemAddressees(project)).items ?? [];
        }
      }));
      const loadedShipments = shipmentResult.shipments ?? [];
      const linkedShipment = urlParam("shipment");
      if (linkedShipment && !loadedShipments.some((shipment) => shipment.id === linkedShipment)) {
        loadedShipments.push(...(await intakeApi.shipments(linkedShipment)).shipments);
      }
      setRows(loadedRows); setShipments(loadedShipments); setError("");
      setAddressees(addresseeResult.addressees ?? []);
      setItemAddressees(deliveries);
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
  const itemOf = (row: Pick<IntakeRow, "project" | "item_index">) => itemAddressees[row.project]?.find((item) => item.item_index === row.item_index);

  const sequences = useMemo(() => shipmentSequence(shipments.flatMap((shipment) => shipment.lines.map((line) => ({ project: line.project, shipment })))), [shipments]);
  /* A shipment is a historical snapshot; unpicked rows use their effective KB delivery. */
  type DeliverySource = Partial<ProjectAddressee> & Partial<DeliveryValues> & { tk_note?: string };
  const deliveryOf = (line: IntakeLine): DeliverySource => line.shipment ?? { ...cardOf(line), ...itemOf(line)?.effective };
  const contactText = (value: string | undefined) => value?.trim().toLowerCase() === "самовывоз" ? "" : value ?? "";
  const deliveryText = (line: IntakeLine, field: DeliveryField): string => {
    const data = deliveryOf(line);
    const pickup = isPickup(data.carrier ?? "") || data.terms === "САМОВЫВОЗ";
    if (field === "receiver") return contactText(data.receiver);
    if (field === "delivery") return pickup ? "Самовывоз" : termsText(data.terms);
    if (field === "carrier") return pickup ? "" : data.carrier ?? "";
    if (field === "address") return pickup ? "" : contactText(data.shipping_point);
    if (field === "phone") return contactText(data.phone);
    if (field === "contact") return contactText(data.contact);
    if (field === "carrierNote") return data.tk_note ?? "";
    return data.note ?? "";
  };
  const deliveryCell = (line: IntakeLine, field: DeliveryField) => {
    const data = deliveryOf(line);
    const text = deliveryText(line, field);
    if (["warehouseNote", "carrierNote", "contact"].includes(field)) return text ? <span title={text}>{text}</span> : dash;
    if ((field === "address" || field === "carrier") && (isPickup(data.carrier ?? "") || data.terms === "САМОВЫВОЗ")) return dash;
    return shippingValue(DELIVERY_TITLES[field], text);
  };
  const trackText = (shipment: DispatchShipment | undefined) =>
    !shipment ? "" : contactText(shipment.track_number);
  const packStatus = (line: IntakeLine) => line.stage !== "accepted" || line.shipment ? ""
    : line.kb.pack_auto_allowed ? AUTO_PACK_LABEL : line.packNow > 0 ? "Разрешено" : "Нужно разрешение";
  /** Отправку держит УПД — какое действие предложить: только № — «Вписать № УПД», нет самого УПД — «Внести УПД». */
  const updGapAction = (shipment: DispatchShipment) => {
    const gaps = new Set(shipment.missing_ship);
    if (gaps.has("тип УПД") || gaps.has("файл УПД")) return { label: "Внести УПД", what: "УПД" };
    if (gaps.has("№ УПД")) return { label: "Вписать № УПД", what: "№ УПД" };
    return null;
  };
  /** «Ждём»: всё, чего не хватает отправке, одним списком рядом со статусом (владелец 23.11: «ощущение, что не хватает
   *  только номера» — список говорит это прямо, а не по ячейкам «не заполнено» далеко справа). */
  const trackGaps = (line: IntakeLine) => {
    const shipment = line.shipment;
    if (!shipment) return [];
    return [...new Set([...shipment.missing_data, ...shipment.missing_ship])];
  };
  /** PL и DN по отдельности: номера нет — «Сформировать» этого документа, есть — ссылка на печать. */
  const docCell = (shipment: DispatchShipment, document: "pl" | "dn") => {
    if ((document === "pl" ? shipment.pl_number : shipment.dn_number) || !canDispatch || !["Собрана", "Упакована"].includes(shipment.status)) return docLink(shipment, document);
    const kind = document === "pl" ? "PL" : "DN";
    return <button type="button" className="secondary small" disabled={Boolean(busy)} aria-label={`Сформировать ${kind} ${shipment.number}`}
      title={document === "pl" ? "Сформировать PL — отправка станет «Упакована»" : "Сформировать DN"}
      onClick={(event) => { event.stopPropagation(); void mutate(`${document}:${shipment.id}`, () => shippingApi.issueDocument(shipment.id, shipment.version, document), `${kind} сформирован: ${shipment.number}`); }}>Сформировать</button>;
  };
  const updRequired = (line: IntakeLine) => !["waiting", "production"].includes(line.stage ?? "");

  /** «Печать» этикетки ГРН акта: та же очередь печати, что у приёмки закупки; статус — под таблицей. */
  async function printLabel(grn: string) {
    if (label?.busy) return;
    setLabel({ grn, result: null, error: "", busy: true });
    try { setLabel({ grn, result: await intakeApi.printLabel(grn), error: "", busy: false }); }
    catch (reason) { setLabel({ grn, result: null, error: failureText(reason, "Этикетка не напечатана"), busy: false }); }
  }
  const mutate: Mutate = async (key, operation, message) => {
    if (busy) return false;
    setBusy(key); setError(""); setNotice("");
    try {
      const result = await operation();
      const outcome = typeof message === "string" ? message : message(result);
      setNotice(typeof outcome === "string" ? outcome : outcome.warning); await reload(); return true;
    }
    catch (reason) { setError(failureText(reason, "Операция не выполнена")); return false; }
    finally { setBusy(""); }
  };
  async function rolledBack(message: string) {
    setRollingBack(null); setError(""); setNotice(message);
    await reload();
  }
  async function accepted(pieceCount: number) {
    setAccepting(null); setError("");
    setNotice(`Принято на склад: ${pieceCount} шт`);
    await reload();
  }
  /** «Упаковать всё» проекта: каждая КБ с разрешением — своими «Упаковать · n»; по одной отправке на группу доставки, как в окне упаковки. */
  async function packAll(project: string, list: Array<{ kb: IntakeRow; quantity: number }>, skipped: number) {
    if (busy) return;
    const total = round2(list.reduce((sum, entry) => sum + entry.quantity, 0));
    const buckets = new Map<number, typeof list>();
    const groups = deliveryGroups((itemAddressees[project] ?? []).filter((item) => list.some((entry) => entry.kb.item_index === item.item_index)));
    for (const entry of list) {
      const index = groups.findIndex((group) => group.items.includes(entry.kb.item_index));
      buckets.set(index, [...(buckets.get(index) ?? []), entry]);
    }
    const question = `Упаковать ${list.length} КБ · ${formatNumber(total)} шт проекта ${project}?`
      + (buckets.size > 1 ? ` У КБ разные данные доставки — будет ${buckets.size} отправки, по одной на группу.` : "")
      + (skipped ? ` Без разрешения пропущено: ${skipped} КБ.` : "");
    if (!window.confirm(question)) return;
    setBusy(`bulk-pack:${project}`); setError(""); setNotice("");
    const numbers: string[] = []; const failed: string[] = [];
    let packedKb = 0; let packedPieces = 0;
    for (const entries of buckets.values()) {
      try {
        const card = await intakeApi.dispatch(project);
        const result = await shippingApi.projectShip({
          project, addressee_version: card.addressee?.version ?? 0, ship: false,
          lines: entries.map((entry) => ({ good_id: String(entry.kb.good_id), version: entry.kb.good_version ?? 0, quantity: String(entry.quantity) })),
          ...(canManage ? { documents: { upd_number: "", upd_date: null, deadline: null } } : {}),
        }, crypto.randomUUID());
        numbers.push(result.shipment.number); packedKb += entries.length; packedPieces += entries.reduce((sum, entry) => sum + entry.quantity, 0);
      } catch (reason) {
        failed.push(`${entries.map((entry) => entry.kb.item_index).join(", ")}: ${failureText(reason, "не упаковано")}`);
      }
    }
    if (packedKb) setNotice(`Упаковано ${packedKb} КБ · ${formatNumber(round2(packedPieces))} шт${numbers.length ? ` · ${numbers.join(", ")}` : ""}`);
    await reload();
    if (failed.length) setError(`Не упаковано ${list.length - packedKb} КБ — ${failed.join("; ")}`);
    setBusy("");
  }
  /** «Отгрузить всё» проекта: готовые отправки — тем же «Отгрузить», что в строке; неготовые перечислены с пробелами. */
  async function shipAll(project: string, ready: DispatchShipment[], skipped: DispatchShipment[]) {
    if (busy) return;
    const count = `${ready.length} ${plural(ready.length, ["отправку", "отправки", "отправок"])}`;
    const question = `Отгрузить ${count} проекта ${project}?`
      + (skipped.length ? ` Не готовы и пропущены: ${skipped.map((shipment) => `${shipment.number} — ${shipment.missing_ship.join(", ") || "не хватает данных"}`).join("; ")}.` : "");
    if (!window.confirm(question)) return;
    setBusy(`bulk-ship:${project}`); setError(""); setNotice("");
    let shippedCount = 0; const failed: string[] = [];
    for (const shipment of ready) {
      try { await shippingApi.action(shipment.id, "ship", shipment.version); shippedCount += 1; }
      catch (reason) { failed.push(`${shipment.number}: ${failureText(reason, "не отгружена")}`); }
    }
    if (shippedCount) setNotice(`Отгружено ${shippedCount} ${plural(shippedCount, ["отправка", "отправки", "отправок"])}`);
    await reload();
    if (failed.length) setError(`Не отгружено ${failed.length} — ${failed.join("; ")}`);
    setBusy("");
  }
  const accept = (kbs: IntakeRow[], quantity: (row: IntakeRow) => number, blank = false) =>
    setAccepting({ rows: kbs, quantities: Object.fromEntries(kbs.map((row) => [keyOf(row), quantity(row)])), blank });

  /** «Принять досрочно» из «В производстве»: сколько можно принять сверх уже сданного цехом. */
  const earlyQuantity = (line: IntakeLine) => round2(Math.min(line.quantity, Math.max(num(line.kb.remaining) - piecesOf(line.kb).ready, 0)));
  /** «Трек…» упакованной отправки строки: одно действие — «Трек…», «Без УПД…» (УПД или его № нет — разрешение по ПИН,
   *  и у набранной с DN: без него DN не печатается) или выключенный «Трек…» с причиной. */
  const trackAction = (shipment: DispatchShipment, title: string) => {
    if (!["Собрана", "Упакована"].includes(shipment.status) || !canDispatch) return null;
    const needing = needsTrack(shipment);
    const ready = needing && (readyForTrack(shipment) || Boolean(shipment.track_number));
    // Держит УПД (владелец 23.11: «непонятно назначение кнопок»): главное — вписать его; запасной путь — отгрузить без него по ПИН.
    const updGap = updGapAction(shipment);
    // Груз уже уехал, а данных нет (владелец 23.11: «она уже отгружена»): руководителю — сразу из строки, не из окна упаковки.
    const bare = rights.canUnship && shipment.lines.some((line) => num(line.picked) > 0) && !readyToShip(shipment) && shipment.missing_ship.length > 0
      ? <button type="button" className="secondary small" title="Груз уже уехал: отметить «Отгружена», недостающее внести позже"
        aria-label={`Уже отгружена ${title}`} onClick={() => setOpened({ kind: "bare", shipment })}>Уже отгружена…</button> : null;
    const enter = updGap && canDispatch ? <button type="button" className="primary small" title={`Открыть данные отправки и вписать ${updGap.what}`}
      aria-label={`${updGap.label} ${title}`} onClick={() => setShipmentData(shipment.id)}>{updGap.label}…</button> : null;
    if (!ready && canWarehouse && onlyUpdBlocks(shipment) && (shipment.status === "Упакована" || Boolean(shipment.dn_number))) return <>{enter}
      {bare ?? <button type="button" className="secondary small intake-print" title="УПД ещё нет — отгрузку без него разрешает уполномоченный по ПИН"
        aria-label={`Отгрузить без УПД ${title}`} onClick={() => setOpened({ kind: "noupd", shipment })}>Отгрузить без УПД…</button>}</>;
    if (enter) return <>{enter}{bare}</>;
    if (!needing || shipment.status !== "Упакована") return bare;
    const gaps = shipment.missing_ship.filter((gap) => gap !== "трек-номер");
    const button = <button type="button" className="secondary small intake-print" disabled={!ready}
      title={ready ? "Вписать трек-номер ТК" : `Трек вносится, когда есть УПД и ТК${gaps.length ? ` — не хватает: ${gaps.join(", ")}` : ""}. Ждём данные менеджера`}
      aria-label={`Трек ${title}`} onClick={() => setOpened({ kind: "track", title, shipment })}>Трек…</button>;
    return <>{button}{bare}</>;
  };
  /** Одно действие строки — по её шагу; число на кнопке — штуки этой строки. */
  const actionCell = (line: IntakeLine) => {
    if (line.stage === "production") {
      const quantity = earlyQuantity(line);
      if (!canWarehouse || !(quantity > 0)) return dash;
      return <button type="button" className="secondary small intake-early" title="Ещё в производстве — принять досрочно"
        aria-label={`Принять досрочно ${who(line)}: ${quantity}`} onClick={() => accept([line.kb], () => quantity, true)}>Принять досрочно · {formatNumber(quantity)}</button>;
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
      if (line.packNow > 0) return <button type="button" className="intake-take-btn"
        title={line.kb.pack_auto_allowed ? AUTO_PACK_LABEL : `Разрешено упаковать по КБ: ${permittedPack(line.kb)}`}
        aria-label={`Упаковать ${who(line)}: ${line.packNow}`} onClick={() => setOpened({ kind: "pack", project: line.project })}>Упаковать · {formatNumber(line.packNow)}</button>;
      if (line.kb.pack_auto_allowed || !canManage) return dash;
      return <button type="button" className="secondary small" aria-label={`Разрешить упаковку ${line.project} ${line.item_index}`}
        onClick={() => setPermissionProject(line.project)}>Разрешить…</button>;
    }
    if (line.stage === "packed") return null;
    if (line.stage === "to_ship" && line.shipment && canDispatch && readyToShip(line.shipment)) return <button type="button" className="primary small intake-print"
      title={`Отгрузить ${line.shipment.number}`} aria-label={`Отгрузить ${who(line)}`} onClick={() => setOpened({ kind: "ship", project: line.project })}>Отгрузить</button>;
    return dash;
  };

  const columns: DataColumn<IntakeLine>[] = [
    { key: "kb", title: "КБ", width: 128, value: (line) => line.item_index, render: (line) => <span className="intake-kb">{line.item_index}</span> },
    { key: "name", title: "Наименование", grow: true, minWidth: 160, maxWidth: 280, wrap: true, value: (line) => line.name,
      render: (line) => <span className="ship-position-name" title={line.name}>{line.name}</span> },
    { key: "stage", title: "Этап позиции", width: 112, value: (line) => line.stage ? stageLabel(line.stage) : line.shipment?.status === "Отменена" ? "Отменена" : "Не набрано",
      render: (line) => <StatusChip tone={line.stage ? STAGE_TONE[line.stage] : "neutral"}>{line.stage ? stageLabel(line.stage) : line.shipment?.status === "Отменена" ? "Отменена" : "Не набрано"}</StatusChip> },
    { key: "quantity", title: QUANTITY_TITLE[tab] ?? "Количество", width: 96, numeric: true, value: (line) => line.quantity,
      render: (line) => `${formatNumber(line.quantity)}${NBSP}${line.unit}` },
    { key: "needed", title: "Заказано", width: 88, numeric: true, value: (line) => num(line.kb.needed), render: (line) => num(line.kb.needed) > 0 ? formatNumber(num(line.kb.needed)) : dash },
    { key: "launched", title: "Запущено", width: 88, numeric: true, value: (line) => num(line.kb.launched), render: (line) => num(line.kb.launched) > 0 ? formatNumber(num(line.kb.launched)) : dash },
    { key: "left", title: "Остаток", width: 88, hint: "Заказано − принято по всем ГРН", numeric: true, value: (line) => leftOf(line.kb), render: (line) => leftOf(line.kb) > 0 ? formatNumber(leftOf(line.kb)) : dash },
    { key: "action", title: ACTION_TITLE[tab] ?? "Действие", width: tab === "production" || tab === "all" ? 192 : tab === "packed" ? 400 : 144,
      value: (line) => line.stage === "accepted" ? line.packNow || (line.shipment ? line.quantity : 0) : line.stage === "ready" ? line.quantity : "", render: actionCell },
    { key: "tasks", title: "Задания", wrap: true, width: 128, maxWidth: 200, value: (line) => line.kb.tasks.map((task) => task.number).join(", "),
      render: (line) => line.kb.tasks.length ? <span className="intake-clip intake-stage muted" title={tasksText(line.kb)}>{line.kb.tasks.map((task) => task.number).join(", ")}</span> : dash },
    { key: "grn", title: "ГРН", wrap: true, width: 136, maxWidth: 200, value: (line) => line.grns.join(", "),
      // Акт приёмки на складе: руководителю ГРН открывает карточку акта с «Откатить приёмку».
      render: (line) => canRollback && line.stage === "accepted" && line.act && !line.shipment
        ? <button type="button" className="link-button intake-act-open" title="Карточка акта приёмки: откат"
          aria-label={`Акт приёмки ${line.act.grn}`} onClick={() => setRollingBack({ row: line.kb, act: line.act!, onStock: line.quantity })}>{line.act.grn}</button>
        : line.grns.join(", ") },
    { key: "quality", title: "Качество", value: line => line.act?.quality_state ?? "", render: line => line.act?.grn_contract_version === 1 || line.act?.quality_state ? <QualityChip passport={line.act} /> : dash },
    { key: "received", title: "Дата приёмки", width: 104, value: (line) => line.act?.received_at ?? "", render: (line) => line.act ? formatDay(line.act.received_at) : dash },
    { key: "label", title: "Этикетка", width: 88, export: false, value: () => "", render: (line) => canWarehouse && line.stage === "accepted" && !line.shipment && line.grns.length === 1
      ? <button type="button" className="link-button intake-grn-print" title="Печать этикетки ГРН" aria-label={`Печать этикетки ${line.grns[0]}`}
        disabled={Boolean(label?.busy)} aria-busy={label?.busy && label.grn === line.grns[0]} onClick={() => void printLabel(line.grns[0])}>Печать</button> : dash },
    { key: "packStatus", title: "Разрешение на упаковку", width: 192, wrap: true, maxWidth: 280, value: packStatus,
      render: (line) => packStatus(line) ? <span className={line.packNow > 0 || line.kb.pack_auto_allowed ? "tone-ok" : "tone-warn"}>{packStatus(line)}</span> : dash },
    { key: "packPermission", title: "Разрешено по КБ", width: 112, numeric: true, value: (line) => line.stage === "accepted" && !line.shipment ? num(line.kb.pack_permission_quantity) : null },
    { key: "packedTotal", title: "Упаковано по КБ", width: 112, numeric: true, value: (line) => line.stage === "accepted" && !line.shipment ? num(line.kb.packed) : null },
    { key: "project", title: "Проект", width: 112, value: (line) => line.project },
    { key: "customer", title: "Заказчик", width: 144, value: (line) => line.shipment?.customer || line.kb.customer },
    { key: "shipment", title: "Отправка", width: 160, value: (line) => line.shipment?.number ?? "",
      render: (line) => line.shipment ? <button type="button" className="link-button" aria-label={`Данные отправки ${line.shipment.number}`}
        onClick={() => setShipmentData(line.shipment!.id)}>{line.shipment.number}</button> : dash },
    { key: "sequence", title: "Отгрузка", width: 96, value: (line) => sequences.get(shipmentGroupKey(line)) ?? "" },
    { key: "status", title: "Статус", width: 112, filter: true, value: (line) => line.shipment?.status ?? "",
      render: (line) => line.shipment ? <StatusChip status={line.shipment.status} /> : dash },
    { key: "created", title: "Создана", width: 104, value: (line) => line.shipment?.created_at ?? "", render: (line) => line.shipment ? formatDay(line.shipment.created_at) : dash },
    { key: "deadline", title: "Срок", width: 104, value: (line) => line.shipment?.deadline ?? line.kb.deadline, render: (line) => formatDay(line.shipment?.deadline ?? line.kb.deadline) || dash },
    { key: "shippedAt", title: "Отгружена", width: 104, value: (line) => line.shipment?.shipped_at ?? "", render: (line) => line.shipment?.shipped_at ? formatDay(line.shipment.shipped_at) : dash },
    ...DELIVERY_COLUMNS.map((key): DataColumn<IntakeLine> => ({ key, title: DELIVERY_TITLES[key], value: (line) => deliveryText(line, key),
      wrap: true, width: key === "address" || key === "warehouseNote" || key === "carrierNote" ? 200 : 128, maxWidth: key === "address" || key === "warehouseNote" || key === "carrierNote" ? 240 : 180,
      render: (line) => deliveryCell(line, key) })),
    { key: "track", title: "Трек", width: 128, value: (line) => trackText(line.shipment), render: (line) => line.shipment ? trackText(line.shipment) || (needsTrack(line.shipment) ? <span className="tone-warn">не заполнено</span> : <span className="muted">не нужен</span>) : dash },
    { key: "upd", title: "УПД", width: 104, value: (line) => updLabel(deliveryOf(line).upd_type),
      render: (line) => updLabel(deliveryOf(line).upd_type) || (updRequired(line) ? <span className="tone-warn">не заполнено</span> : dash) },
    { key: "updNumber", title: "№ УПД", width: 128, value: (line) => deliveryOf(line).upd_number ?? "",
      render: (line) => deliveryOf(line).upd_number || (updRequired(line) && deliveryOf(line).upd_type !== "БЕЗ УПД" ? shippingValue("№ УПД", "") : dash) },
    { key: "updDate", title: "Дата УПД", width: 104, value: (line) => deliveryOf(line).upd_date ?? "", render: (line) => deliveryOf(line).upd_date ? formatDay(deliveryOf(line).upd_date) : dash },
    { key: "updFile", title: "Файл УПД", width: 104, value: (line) => deliveryOf(line).upd_url ?? "", render: (line) => deliveryOf(line).upd_url
      ? <a href={deliveryOf(line).upd_url} target="_blank" rel="noreferrer">файл</a>
      : updRequired(line) && deliveryOf(line).upd_type && !["ЭДО", "БЕЗ УПД"].includes(deliveryOf(line).upd_type!) ? shippingValue("Файл УПД", "") : dash },
    { key: "trackGaps", title: "Ждём", hint: "Чего не хватает, чтобы отгрузить", wrap: true, width: 160, maxWidth: 240,
      value: (line) => trackGaps(line).join(", "), render: (line) => trackGaps(line).length ? <span className="tone-warn">{trackGaps(line).join(", ")}</span> : dash },
    { key: "pl", title: "PL", width: 136, value: (line) => line.shipment?.pl_number ?? "", render: (line) => line.shipment ? docCell(line.shipment, "pl") : dash },
    { key: "dn", title: "DN", width: 136, value: (line) => line.shipment?.dn_number ?? "", render: (line) => line.shipment ? docCell(line.shipment, "dn") : dash },
    { key: "deliveryEdit", title: "Доставка КБ", width: 152, export: false, value: () => "", render: (line) => canDispatch && itemOf(line) ? <button type="button" className="secondary small"
      aria-label={`Доставка ${who(line)}`} onClick={() => setDeliveryEdit({ project: line.project, items: [itemOf(line)!] })}>Изменить доставку</button> : dash },
  ];
  const actionless = (tab === "production" && !canWarehouse) || (["accepted", "to_ship"].includes(tab) && !canDispatch);
  const leading = [...TAB_COLUMNS[tab], ...(tab === "shipped" && canDispatch ? ["action"] : [])].filter((key) => key !== "action" || !actionless);
  // Что держит отправку — сразу за «Статусом» (владелец 23.11: «почему зависло» — пустой № УПД был за прокруткой):
  // «Ждём для трека», УПД, трек, PL/DN, потом адресные данные.
  const shipping = ["project", "customer", "shipment", "sequence", "status", "created", "deadline", "shippedAt", "trackGaps", "upd", "updNumber", "updDate", "updFile", "track", "pl", "dn", ...DELIVERY_COLUMNS, "deliveryEdit"];
  const hidden = new Set(["project", "customer", "shipment", "sequence", "created", "deadline", "stage", ...(tab === "shipped" ? [] : ["shippedAt"])]);
  const shipmentFields = new Set(["shipment", "sequence", "status", "created", "deadline", "shippedAt", ...DELIVERY_COLUMNS, "track", "trackGaps", "upd", "updNumber", "updDate", "updFile", "pl", "dn"]);
  const visibleColumns = [...new Set([...leading, ...shipping, "stage"])].flatMap((key) => columns.filter((column) => column.key === key))
    .map((column): DataColumn<IntakeLine> => ({ ...column, hidden: hidden.has(column.key),
      export: column.export ?? (shipmentFields.has(column.key) || hidden.has(column.key)),
      render: (line) => ["project", "customer"].includes(column.key) || (line.shipment && shipmentFields.has(column.key))
        ? null : column.render ? column.render(line) : cellContent(column, column.value(line)),
    }));

  const projectLabel = (_key: string, groupRows: IntakeLine[]) => {
    const first = groupRows[0];
    const project = first.project;
    const bulk = tab === "ready" || tab === "all" ? [...new Map(groupRows.filter((line) => !line.shipment && (tab === "ready" ? line.stage === "ready" : num(line.kb.remaining) > 0))
      .map((line) => [kbKey(line.kb), line.kb])).values()] : [];
    const quantity = (row: IntakeRow) => tab === "ready" ? piecesOf(row).ready : num(row.remaining);
    const total = round2(bulk.reduce((sum, row) => sum + quantity(row), 0));
    // «Принято»: КБ проекта, которые можно упаковать сейчас (по «Упаковать · n» строк), и сколько ждут разрешения.
    const packKbs = new Map<string, { kb: IntakeRow; quantity: number }>(); const blockedKbs = new Set<string>();
    if (tab === "accepted" && canDispatch) for (const line of groupRows.filter((item) => item.stage === "accepted" && !item.shipment)) {
      const key = kbKey(line.kb); const quantity = round2((packKbs.get(key)?.quantity ?? 0) + line.packNow);
      if (quantity > 0 && line.kb.good_id) packKbs.set(key, { kb: line.kb, quantity: Math.min(quantity, permittedPack(line.kb)) }); else if (!packKbs.has(key)) blockedKbs.add(key);
    }
    for (const key of packKbs.keys()) blockedKbs.delete(key);
    const packList = [...packKbs.values()]; const packTotal = round2(packList.reduce((sum, entry) => sum + entry.quantity, 0));
    // «Упаковано» / «На отгрузку»: готовые отправки проекта и неготовые с их пробелами.
    const projectShipments = (tab === "packed" || tab === "to_ship") && canDispatch
      ? shipments.filter((shipment) => ofProject(shipment, project) && ["Собрана", "Упакована"].includes(shipment.status)) : [];
    const readyShipments = canDispatch ? projectShipments.filter(readyToShip) : [];
    const blockedShipments = projectShipments.filter((shipment) => !readyToShip(shipment));
    return <span className="ship-group-head">
      <strong>{project}</strong><span> · {head[project]?.customer || first.shipment?.customer || "—"}</span>
      <button type="button" className="secondary small" aria-label={`Изменить данные отправки ${project}`}
        onClick={() => setProjectData(project)}>Изменить данные проекта</button>
      {canDispatch && Boolean(itemAddressees[project]?.length) && <button type="button" className="secondary small"
        aria-label={`Всем КБ проекта ${project}`} onClick={() => setDeliveryEdit({ project, items: itemAddressees[project] })}>Всем КБ проекта</button>}
      {canWarehouse && bulk.length > 1 && <button type="button" className="primary small" onClick={() => accept(bulk, quantity)}>
        Принять всё: {bulk.length} КБ · {formatNumber(total)} шт</button>}
      {packList.length > 1 && <button type="button" className="primary small" disabled={Boolean(busy)} title={blockedKbs.size ? `Без разрешения упаковки пропущено: ${blockedKbs.size} КБ` : "Упаковать каждую КБ проекта на столько, сколько указано в её «Упаковать»"}
        onClick={() => void packAll(project, packList, blockedKbs.size)}>
        Упаковать всё: {packList.length} КБ · {formatNumber(packTotal)} шт</button>}
      {packList.length > 1 && blockedKbs.size > 0 && <span className="muted ship-group-hint">без разрешения: {blockedKbs.size} КБ</span>}
      {readyShipments.length > 1 && <button type="button" className="primary small" disabled={Boolean(busy)}
        title={blockedShipments.length ? `Не готовы и будут пропущены: ${blockedShipments.map((shipment) => `${shipment.number} — ${shipment.missing_ship.join(", ")}`).join("; ")}` : "Отгрузить все готовые отправки проекта"}
        onClick={() => void shipAll(project, readyShipments, blockedShipments)}>
        Отгрузить всё: {readyShipments.length} {plural(readyShipments.length, ["отправка", "отправки", "отправок"])}</button>}
      {readyShipments.length > 1 && blockedShipments.length > 0 && <span className="muted ship-group-hint" title={blockedShipments.map((shipment) => `${shipment.number} — ${shipment.missing_ship.join(", ")}`).join("; ")}>не готовы: {blockedShipments.length}</span>}
    </span>;
  };
  const shipmentCells = (_key: string, groupRows: IntakeLine[], _filtered: IntakeLine[], visible: DataColumn<IntakeLine>[]) => {
    const first = groupRows[0];
    const shipment = first.shipment;
    if (!shipment) return null;
    const cells: Partial<Record<string, ReactNode>> = {};
    for (const column of columns) if (shipmentFields.has(column.key)) cells[column.key] = column.render
      ? column.render(first) : cellContent(column, column.value(first));
    const visibleKeys = new Set(visible.map((column) => column.key));
    if (!visibleKeys.has("shipment")) cells.name = cells.shipment;
    const ordinal = `Отгрузка ${sequences.get(shipmentGroupKey(first))}`;
    cells[visibleKeys.has("sequence") ? "sequence" : "kb"] = ordinal;
    const totals = new Map<string, number>();
    for (const line of shipment.lines) if (line.project === first.project) totals.set(line.unit, round2((totals.get(line.unit) ?? 0) + num(line.picked)));
    cells.quantity = [...totals].map(([unit, quantity]) => `${formatNumber(quantity)}${NBSP}${unit}`).join(", ");
    cells.action = <span className="ship-group-head">
      {(first.stage === "packed" || shipment.status === "Собрана") && trackAction(shipment, shipment.number)}
      {canDispatch && ["Новая", "Собрана", "Упакована"].includes(shipment.status) && <button type="button" className="secondary small"
        title="Окно упаковки: короба, PL и DN этой отправки" aria-label={`${shipment.status === "Упакована" ? "Изменить упаковку" : "Открыть упаковку"} ${shipment.number}`}
        onClick={() => setOpened({ kind: "pack", project: first.project })}>{shipment.status === "Упакована" ? "Изменить упаковку…" : "Открыть упаковку…"}</button>}
      {canDispatch && ["Отгружена", "Закрыта"].includes(shipment.status) && <button type="button" className="secondary small"
        onClick={() => setOpened({ kind: "ship", project: first.project })}>Действия</button>}
    </span>;
    return cells;
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
    {(error || docError) && <div className="error-banner" role="alert">{error || docError}</div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    {!loaded ? <div className="empty">Загрузка…</div> : <>
      <div className="seg-tabs" role="tablist" aria-label="Шаг">{TABS.map(([key, text]) =>
        <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : undefined} onClick={() => changeTab(key)}
          title="Число — КБ в шаге; во «Все» КБ считается один раз">
          {text}<span className="page-count"> {board.counts[key]}</span></button>)}</div>
      <DataTable key={tab} label="Приёмка с производства" storageKey={`production-intake:v7:${tab}`} rows={lines} columns={visibleColumns} rowKey={(line) => line.id}
        bulkActions={canDispatch ? [{ label: "Задать доставку выбранным КБ", disabled: (selected) => selected.some((line) => !itemOf(line)),
          run: (selected) => {
            if (new Set(selected.map((line) => line.project)).size !== 1) { setError("Для массовой доставки выберите КБ одного проекта."); return; }
            setDeliveryEdit({ project: selected[0].project, items: [...new Map(selected.map((line) => [line.item_index, itemOf(line)!])).values()] });
          } }] : []}
        groupParent={(line) => line.project} groupParentLabel={projectLabel} groupBy={shipmentGroupKey} groupCells={shipmentCells} groupSort exportName="Приёмка с производства"
        searchText={(line) => line.shipment ? shipmentSearch({ ...line.shipment, lines: line.shipment.lines.filter((position) => line.id === `${line.shipment!.id}:${position.id}`) }) : []} initialQuery={urlParam("q")} searchPlaceholder="Проект, КБ, наименование, ГРН или задание"
        rowClassName={(line) => tab === "all" && !(line.stage && hasWork(line.stage, line.kb, piecesOf(line.kb))) ? "ship-line-off" : ""}
        emptyText={board.rows.length ? EMPTY[tab] : EMPTY.all} />
      {label && <div className="intake-print-status"><strong>Этикетка {label.grn}</strong><PrintOutcome result={label.result} error={label.error} /></div>}
    </>}
    {accepting && <IntakeWindow rows={accepting.rows} quantities={accepting.quantities} blank={accepting.blank} close={() => setAccepting(null)} onDone={accepted} />}
    {rollingBack && <RollbackActWindow row={rollingBack.row} act={rollingBack.act} onStock={rollingBack.onStock} close={() => setRollingBack(null)} onDone={rolledBack} />}
    {opened?.kind === "pack" && <PackWindow project={opened.project} rows={projectRows(opened.project)} shipments={projectShips(opened.project)} rights={rights} reload={reload} close={() => setOpened(null)} />}
    {opened?.kind === "ship" && <ShipWindow project={opened.project} shipments={projectShips(opened.project)} rights={rights} reload={reload} close={() => setOpened(null)} />}
    {opened?.kind === "track" && <TrackWindow title={opened.title} shipments={[shipments.find((shipment) => shipment.id === opened.shipment.id) ?? opened.shipment]} reload={reload} close={() => setOpened(null)} />}
    {opened?.kind === "noupd" && <NoUpdWindow shipment={opened.shipment} mutate={mutate} close={() => setOpened(null)} />}
    {opened?.kind === "bare" && <ShipWithoutDataWindow shipment={opened.shipment} gaps={opened.shipment.missing_ship} mutate={mutate} close={() => setOpened(null)} />}
    {header === "journal" && <JournalWindow shipments={shipments} selectedShipment={shipments.find((shipment) => shipment.id === urlParam("shipment"))} initialQuery={urlParam("q")} close={() => setHeader(null)} />}
    {header === "good" && <RegisterGoodWindow reload={reload} close={() => setHeader(null)} />}
    {header === "request" && <CreateShipmentWindow reload={reload} close={() => setHeader(null)} />}
    {permissionProject && <PackingPermissionWindow project={permissionProject} editable={canManage} close={() => setPermissionProject(null)} saved={reload} />}
    {shipmentData && shipments.find((shipment) => shipment.id === shipmentData) && <ShipmentDataWindow
      key={shipmentData} shipment={shipments.find((shipment) => shipment.id === shipmentData)!} canEdit={canDispatch}
      close={() => setShipmentData(null)} onSaved={reload} />}
    {projectData && <DispatchCard project={projectData} customer={head[projectData]?.customer} stage={tab === "all" ? undefined : tab} canEdit={canDispatch} onSaved={() => void reload()} close={() => setProjectData(null)} />}
    {deliveryEdit && <ItemDeliveryWindow project={deliveryEdit.project} items={deliveryEdit.items} close={() => setDeliveryEdit(null)} onSaved={reload} />}
  </section>;
}
