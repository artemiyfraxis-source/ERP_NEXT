import { useEffect, useMemo, useRef, useState } from "react";
import { failureText, productionApi, shippingApi, type FinishedGood, type FinishedGoodsReadyBox, type Principal, type ProjectAddressee } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { ShipmentDataWindow } from "./ShipmentData";
import { DISPATCH_STAGES } from "./dispatchStage";
import { FinishedGoodsHandoff } from "./FinishedGoodsHandoff";
import { formatDay } from "./format";
import { canPack, hasWork, kbKey, num, ofKb, ofProject, packable, round2, SHIPPED_DAYS, shippedOnlyRows, stagePieces, type Pieces, type Tab } from "./intakeStages";
import { isPickup, termsText } from "./carriers";
import { canAct, canSee, hasFullAccess } from "./navigation";
import { intakeApi, type DispatchShipment, type IntakeAct, type IntakeRow } from "./productionIntakeApi";
import { IntakeWindow, leftOf } from "./IntakeWindow";
import { PrintOutcome, type PrintResult } from "./LabelPrint";
import { PackWindow } from "./PackWindow";
import { PrintSetupButton } from "./PrintSetupWindow";
import { ACTIVE, needsTrack, NoUpdWindow, onlyUpdBlocks, readyForTrack, type CardRights } from "./ShipmentCard";
import { CreateShipmentWindow, JournalWindow, RegisterGoodWindow } from "./ShipmentJournal";
import { ShipmentDocsWindow, updState } from "./ShipmentDocs";
import { openShippingForms } from "./shippingPrint";
import { useCanPrint } from "./rights";
import { ShipWindow } from "./ShipWindow";
import { TrackWindow } from "./TrackWindow";
import "./productionIntake.css";

/* «Приёмка с производства» (владелец 14.10, 29.10.2026): всё, что в работе, по проектам — и приёмка, и упаковка, и
   отгрузка на одном экране, экран «Отгрузка» убран. Вкладки — шаги пути готовой продукции; КБ, у которой штуки сразу в
   нескольких шагах, видна в каждом. Как в приёмке: число в колонке — кнопка, действие — во всплывающем окне.
   «Можно принять» — окно приёмки; «Упаковано» — упаковка (после решения менеджера «упаковывать»), PL/DN, короба,
   этикетки; «Трек…» — трек ТК, когда есть УПД и ТК; «Отгружено» — отправки проекта, «Отгрузить», без УПД — по ПИН. */

const TABS: ReadonlyArray<readonly [Tab, string]> = [...DISPATCH_STAGES, ["all", "Все"]];
/** Старые ссылки `?section=shipping&tab=…` и вкладки бывшей «Отгрузки» ведут в свои шаги. */
const TAB_ALIASES: Record<string, Tab> = { stock: "accepted", pack: "accepted", shipments: "packed", ship: "to_ship", done: "shipped", production: "production" };
/** Акты приёмки (зелёные строки с ГРН) — там, где о приёмке речь. */
const ACT_TABS: Tab[] = ["accepted", "all"];
const DELIVERY_COLUMNS = ["receiver", "delivery", "carrier", "address", "phone", "warehouseNote"] as const;
/** Колонки и действие каждой вкладки (владелец 29.10: «зачем лишняя инфа, кнопки 2, приёмка в отгруженных»): шаг показывает
 *  только своё; полный набор — во «Все». «Проект» — скрытая колонка для поиска и группировки. */
const TAB_COLUMNS: Record<Exclude<Tab, "all">, string[]> = {
  waiting: ["project", "kb", "name", "needed"],
  // «В работе» принимается до заказанного и без «Готово» (владелец 28.10 и 29.10: «кладовщик не может принять без статуса»).
  // Кладовщику задания цеха не нужны (владелец 29.10: «зачем тут Алексею задания»): номера — в подсказке кнопки «Принять»
  // и в «Колонках».
  production: ["project", "kb", "name", "needed", "launched", "remaining"],
  ready: ["project", "kb", "name", "needed", "remaining"],
  accepted: ["project", "kb", "name", "accepted", "received", "grn", "packed"],
  packed: ["project", "kb", "name", "grn", "packed", ...DELIVERY_COLUMNS, "track", "docs"],
  to_ship: ["project", "kb", "name", "grn", "packed", ...DELIVERY_COLUMNS, "track", "docs", "ship"],
  shipped: ["project", "kb", "name", "grn", "sent", ...DELIVERY_COLUMNS, "track", "shippedAt"],
};
const EMPTY: Record<Tab, string> = {
  waiting: "В ожидании производства ничего нет.", production: "В производстве ничего нет.", ready: "Готового к приёмке нет.",
  accepted: "Принятого и не упакованного нет.", packed: "Упакованного, но не готового к отгрузке нет.",
  to_ship: "Готового к отгрузке нет — нужны УПД, ТК и трек.", shipped: `Отгруженного за ${SHIPPED_DAYS} дней нет — история в «Журнале».`, all: "Нет КБ в работе",
};

const keyOf = (row: IntakeRow) => `${row.project}\u0000${row.item_index}`;
const lineKey = (row: IntakeLine) => row.act ? `${keyOf(row)}\u0000${row.act.grn}` : keyOf(row);
const SHIPPED_STATUS = ["Отгружена", "Закрыта"];
const dash = <span className="muted">—</span>;
const count = (value: string) => num(value) > 0 ? value : dash;

function tasksText(row: IntakeRow) {
  return row.tasks.map((task) => `${task.number}${task.site ? ` · ${task.site}` : ""} · сдано ${task.delivered} из ${task.quantity}`).join("\n");
}

function urlTab(): { tab: Tab | null; journal: boolean } {
  const wanted = new URLSearchParams(window.location.search).get("tab") ?? "";
  if (wanted === "journal") return { tab: null, journal: true };
  const tab = TAB_ALIASES[wanted] ?? wanted;
  return { tab: TABS.some(([key]) => key === tab) ? tab as Tab : null, journal: false };
}

/** Акты остаются отдельными строками; первая несёт общие действия КБ. Ниже — только фактический остаток к приёмке. */
type IntakeLine = IntakeRow & { act?: IntakeAct; left: number; kbActions: boolean };
function expand(row: IntakeRow, withActs: boolean, withRemainder: boolean): IntakeLine[] {
  const acts = withActs ? row.acts ?? [] : [];
  const left = leftOf(row);
  const lines: IntakeLine[] = acts.map((act, index) => ({
    ...row, act, kbActions: index === 0, left: 0, stage: "", launched: "0", delivered: "0", accepted: act.quantity, remaining: "0",
    packed: index === 0 ? row.packed : "0", sent: index === 0 ? row.sent : "0",
    grn: act.grn, location: act.location, tasks: [],
  }));
  if (!acts.length) lines.push({ ...row, left, kbActions: true });
  else if (withRemainder && (left > 0 || num(row.remaining) > 0)) lines.push({ ...row, left, kbActions: false, accepted: "0", packed: "0", sent: "0", grn: null, location: "" });
  return lines;
}

/** Первая вкладка, где у кладовщика есть работа; без неё — первая непустая; пусто везде — «Все». */
function defaultTab(rows: IntakeRow[], pieces: (row: IntakeRow) => Pieces, warehouse: boolean): Tab {
  if (warehouse) for (const [stage] of DISPATCH_STAGES) if (rows.some((row) => hasWork(stage, row, pieces(row)))) return stage;
  for (const [stage] of DISPATCH_STAGES) if (rows.some((row) => pieces(row)[stage] > 0)) return stage;
  return "all";
}

type Opened = { kind: "pack" | "ship"; project: string } | { kind: "track"; row: IntakeRow } | { kind: "noupd"; shipment: DispatchShipment } | { kind: "docs"; id: string };

export function ProductionIntakePage({ principal }: { principal: Principal }) {
  // Принимает, упаковывает и отгружает кладовщик («Отгрузка — склад»); менеджер («Отгрузка») — данные отправки, заявки.
  const canWarehouse = canAct(principal, "shipping_goods");
  const canManage = canAct(principal, "shipping");
  const canDispatch = canManage || canWarehouse;
  const canPrintDocs = useCanPrint();
  const rights: CardRights = { canManage, canWarehouse, canEdit: canDispatch, canUnship: hasFullAccess(principal) };
  const fromUrl = useRef(urlTab());
  const [rows, setRows] = useState<IntakeRow[]>([]);
  const [shipments, setShipments] = useState<DispatchShipment[]>([]);
  const [addressees, setAddressees] = useState<ProjectAddressee[]>([]);
  const [goods, setGoods] = useState<FinishedGood[]>([]);
  const [boxes, setBoxes] = useState<FinishedGoodsReadyBox[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [tab, setTab] = useState<Tab>(fromUrl.current.tab ?? "ready");
  const tabChosen = useRef(Boolean(fromUrl.current.tab));
  const [intake, setIntake] = useState<IntakeRow[] | null>(null);
  const [opened, setOpened] = useState<Opened | null>(null);
  const [header, setHeader] = useState<"journal" | "good" | "request" | null>(fromUrl.current.journal ? "journal" : null);
  const [label, setLabel] = useState<{ grn: string; result: PrintResult | null; error: string; busy: boolean } | null>(null);
  const [card, setCard] = useState<DispatchShipment | null>(null);

  async function reload() {
    try {
      const [intakeResult, shipmentResult, goodsResult, boxesResult, addresseeResult] = await Promise.all([
        intakeApi.rows(),
        intakeApi.shipments(),
        canWarehouse ? shippingApi.goods() : Promise.resolve({ goods: [] as FinishedGood[] }),
        canWarehouse ? productionApi.finishedGoodsReady() : Promise.resolve({ boxes: [] as FinishedGoodsReadyBox[] }),
        shippingApi.addressees(),
      ]);
      const loadedRows = intakeResult.rows ?? [];
      const loadedShipments = shipmentResult.shipments ?? [];
      setRows(loadedRows); setShipments(loadedShipments); setGoods(goodsResult.goods ?? []); setBoxes(boxesResult.boxes ?? []); setError("");
      setAddressees(addresseeResult.addressees ?? []);
      if (!tabChosen.current) {
        const all = [...loadedRows, ...shippedOnlyRows(loadedRows, loadedShipments)];
        setTab(defaultTab(all, (row) => stagePieces(row, loadedShipments), canWarehouse));
      }
    } catch (reason) {
      setError(failureText(reason, "Не удалось загрузить список КБ в работе"));
    } finally { setLoaded(true); }
  }
  useEffect(() => { void reload(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const allRows = useMemo(() => [...rows, ...shippedOnlyRows(rows, shipments)], [rows, shipments]);
  const pieces = useMemo(() => new Map(allRows.map((row) => [kbKey(row), stagePieces(row, shipments)])), [allRows, shipments]);
  const piecesOf = (row: IntakeRow) => pieces.get(kbKey(row)) ?? stagePieces(row, shipments);
  const inTab = (row: IntakeRow, key: Tab) => key === "all" || piecesOf(row)[key] > 0;
  const shown = useMemo(() => allRows.filter((row) => inTab(row, tab)).flatMap((row) => expand(row, ACT_TABS.includes(tab), tab === "all")),
    [allRows, tab, pieces]); // eslint-disable-line react-hooks/exhaustive-deps
  const head = useMemo(() => {
    const byProject: Record<string, IntakeRow> = {};
    for (const row of allRows) if (!byProject[row.project]) byProject[row.project] = row;
    return byProject;
  }, [allRows]);
  const live = (shipment: DispatchShipment) => shipment.status !== "Отменена";
  const kbShips = (row: IntakeRow) => shipments.filter((shipment) => live(shipment) && ofKb(shipment, row));
  const addresseeByProject = useMemo(() => new Map(addressees.map((card) => [card.project.trim().toUpperCase(), card])), [addressees]);
  const cardOf = (row: IntakeRow) => addresseeByProject.get(row.project.trim().toUpperCase());
  const shownShips = (row: IntakeRow) => kbShips(row).filter((shipment) => tab === "shipped" ? SHIPPED_STATUS.includes(shipment.status)
    : tab === "packed" ? shipment.status === "Упакована" && !shipment.ready_to_ship
      : tab === "to_ship" ? shipment.status === "Упакована" && shipment.ready_to_ship
        : tab === "all" || ACTIVE.includes(shipment.status));
  const grnGroups = (row: IntakeRow) => {
    const ships = ["packed", "to_ship", "shipped"].includes(tab) ? shownShips(row) : [];
    return ships.length ? ships.map((shipment) => ({ id: shipment.id, number: shipment.number,
      grns: [...new Set(shipment.lines.filter((line) => kbKey(line) === kbKey(row)).map((line) => line.grn).filter((grn): grn is string => Boolean(grn)))],
    })) : [{ id: "", number: "", grns: row.grn ? [row.grn] : [] }];
  };
  const deliveryOf = (row: IntakeRow) => {
    const ships = ["packed", "to_ship", "shipped", "all"].includes(tab) ? shownShips(row) : [];
    return ships.length ? ships : [cardOf(row)].filter((card): card is ProjectAddressee => Boolean(card));
  };
  const deliveryText = (row: IntakeRow, field: typeof DELIVERY_COLUMNS[number], data: ProjectAddressee | DispatchShipment): string => {
    if (field === "receiver") return [data.receiver, data.contact].filter(Boolean).join(" · ");
    if (field === "delivery") return isPickup(data.carrier) ? "Самовывоз" : termsText(data.terms);
    if (field === "carrier") return data.carrier;
    if (field === "address") return isPickup(data.carrier) ? "Не требуется" : data.shipping_point;
    if (field === "phone") return data.phone;
    return [...new Set([cardOf(row)?.note, data.note, "tk_note" in data ? data.tk_note : ""].map((text) => text?.trim()).filter(Boolean))].join("\n");
  };
  const deliveryCell = (row: IntakeLine, field: typeof DELIVERY_COLUMNS[number]) => {
    const entries = deliveryOf(row);
    return <span className="intake-delivery-list">{entries.length ? entries.map((data, index) => {
      const text = deliveryText(row, field, data);
      return <span className="intake-delivery-line" key={"id" in data ? data.id : data.project} title={"number" in data ? data.number : undefined}
        data-shipment={"id" in data ? data.id : undefined}>
        {entries.length > 1 && <small className="muted intake-shipment-index">{index + 1}</small>}
        {field === "receiver" ? <span><span>{data.receiver || "—"}</span>{data.contact && <small className="muted intake-contact">{data.contact}</small>}</span>
          : field === "warehouseNote" ? <span>{text ? text.split("\n").map((note, line) => <span className="intake-note-line" key={line}>{note}</span>) : dash}</span>
            : <span>{text || "—"}</span>}
      </span>;
    }) : dash}</span>;
  };
  const readyShips = (row: IntakeRow) => kbShips(row).filter((shipment) => shipment.status === "Упакована" && shipment.ready_to_ship);
  const shippedAt = (row: IntakeRow) => kbShips(row).filter((shipment) => SHIPPED_STATUS.includes(shipment.status)).map((shipment) => String(shipment.shipped_at ?? "")).sort().pop() ?? "";
  const trackShips = (row: IntakeRow) => kbShips(row).filter((shipment) => shipment.status === "Упакована" && needsTrack(shipment));

  /** Общие действия КБ считают количества по серверной строке, а не по одному акту. */
  const source = (line: IntakeRow) => allRows.find((row) => keyOf(row) === keyOf(line)) ?? line;

  async function accepted(pieceCount: number) {
    setIntake(null); setError("");
    setNotice(`Принято на склад: ${pieceCount} шт`);
    await reload();
  }

  /** «Печать этикетки» ГРН: та же очередь печати, что у приёмки закупки; статус — под таблицей. */
  async function printLabel(row: IntakeLine) {
    if (!row.grn || label?.busy) return;
    const grn = row.grn;
    setLabel({ grn, result: null, error: "", busy: true });
    try {
      const result = await intakeApi.printLabel(grn);
      setLabel({ grn, result, error: "", busy: false });
    } catch (reason) {
      setLabel({ grn, result: null, error: failureText(reason, "Этикетка не напечатана"), busy: false });
    }
  }

  async function printDocument(shipment: DispatchShipment, document: "pl" | "dn") {
    if (!canPrintDocs || busy) return;
    setBusy(`print:${shipment.id}:${document}`); setError("");
    try { openShippingForms((await shippingApi.print(shipment.id, document)).forms); }
    catch (reason) { setError(failureText(reason, "Не удалось подготовить печать")); }
    finally { setBusy(""); }
  }

  const mutateBoxes = async (key: string, operation: () => Promise<unknown>, message: string) => {
    if (busy) return false;
    setBusy(key); setError(""); setNotice("");
    try { await operation(); setNotice(message); await reload(); return true; }
    catch (reason) { setError(failureText(reason, "Операция не выполнена")); return false; }
    finally { setBusy(""); }
  };

  const who = (row: IntakeRow) => `${row.project} ${row.item_index}`;
  /** «Упаковано»: число — кнопка окна упаковки. Подсвечена, когда есть что упаковать и менеджер решила «упаковывать». */
  const packCell = (line: IntakeLine) => {
    if (!line.kbActions) return dash;
    // «Упаковано» и «На отгрузку»: только число штук своего шага, действие — в соседней колонке.
    if (tab === "packed" || tab === "to_ship") return count(String(piecesOf(line)[tab]));
    if (!canDispatch) return count(line.packed);
    const row = source(line);
    const avail = packable(row);
    const open = kbShips(row).some((shipment) => ACTIVE.includes(shipment.status));
    const waitDecision = avail > 0 && !row.pack_decided;
    const openPack = () => setOpened({ kind: "pack", project: row.project });
    if (canPack(row)) return <button type="button" className="intake-take-btn" title={`Можно упаковать: ${avail}`}
      aria-label={`Упаковать ${who(row)}: упаковано ${num(row.packed)}, можно ${avail}`} onClick={openPack}>{num(row.packed)}</button>;
    if (open) return <button type="button" className="intake-num-btn" title={waitDecision ? "Отправки проекта на упаковке; упаковать ещё — после решения менеджера «упаковывать»" : "Отправки проекта на упаковке: PL/DN, короба, этикетки"}
      aria-label={`Упаковка ${who(row)}: упаковано ${num(row.packed)}`} onClick={openPack}>{num(row.packed)}</button>;
    if (waitDecision) return <button type="button" className="intake-num-btn" disabled title="Упаковать можно после решения менеджера «упаковывать»"
      aria-label={`Упаковать ${who(row)}: ждёт решения менеджера`}>{num(row.packed)}</button>;
    return count(row.packed);
  };
  /** «Трек»: у упакованных отправок с доставкой ТК; вписать можно, когда есть УПД и ТК. */
  const trackCell = (row: IntakeLine, action = true) => {
    if (!row.kbActions) return dash;
    const ships = shownShips(row);
    const needing = action ? ships.filter((shipment) => shipment.status === "Упакована" && needsTrack(shipment)) : [];
    const text = ships.length ? <span className="intake-delivery-list">{ships.map((shipment, index) => <span className="intake-delivery-line intake-tracks" key={shipment.id}
      data-shipment={shipment.id} title={shipment.number}>{ships.length > 1 && <small className="muted intake-shipment-index">{index + 1}</small>}
      <span>{shipment.track_number || (!needsTrack(shipment) ? "Не требуется" : "—")}</span></span>)}</span> : null;
    if (!canDispatch || !action) return text ?? dash;
    // Одно действие на строку: «Трек…», когда можно; «Без УПД…», когда держит только УПД; иначе выключенный «Трек…» с причиной.
    const packedShips = ships.filter((shipment) => shipment.status === "Упакована");
    const ready = needing.some((shipment) => readyForTrack(shipment) || Boolean(shipment.track_number));
    const noUpd = canWarehouse && !ready ? packedShips.find(onlyUpdBlocks) : undefined;
    if (noUpd) return <span className="intake-track-cell">{text}
      <button type="button" className="secondary small intake-print" title="Отгрузка без УПД: разрешает уполномоченный по ПИН" aria-label={`Без УПД ${who(row)}`}
        onClick={() => setOpened({ kind: "noupd", shipment: noUpd })}>Без УПД…</button></span>;
    if (!needing.length) return text ?? dash;
    const gaps = [...new Set(needing.flatMap((shipment) => shipment.missing_ship))];
    return <span className="intake-track-cell">{text}
      <button type="button" className="secondary small intake-print" disabled={!ready}
        title={ready ? "Вписать трек-номер ТК" : `Трек вносится, когда есть УПД и ТК${gaps.length ? ` — не хватает: ${gaps.join(", ")}` : ""}. Ждём данные менеджера`}
        aria-label={`Трек ${who(row)}`} onClick={() => setOpened({ kind: "track", row: source(row) })}>Трек…</button></span>;
  };
  /** Каждая отправка держит свои PL, DN и УПД; формирование и перевыпуск остаются явными действиями. */
  const docsShips = (row: IntakeLine) => row.kbActions ? shownShips(row).filter((shipment) => shipment.status === "Упакована") : [];
  const docsCell = (row: IntakeLine) => {
    const ships = docsShips(row);
    if (!ships.length) return dash;
    return <span className="intake-docs">{ships.map((shipment) => {
      const upd = updState(shipment);
      return <span className="intake-docs-line" key={shipment.id} data-shipment={shipment.id}>
        {ships.length > 1 && <span className="intake-ship-no muted">{ships.indexOf(shipment) + 1} · {shipment.number}</span>}
        {!shipment.pl_number
          ? (rights.canEdit
            ? <button type="button" className="secondary small intake-print" aria-label={`Сформировать отгрузочные ${shipment.number}`} onClick={() => setOpened({ kind: "pack", project: row.project })}>Сформировать отгрузочные</button>
            : <span className="muted">не сформированы</span>)
          : <>
            <span className="intake-doc-actions">
              {canPrintDocs ? <>
                <button type="button" className="link-button intake-doc-action" aria-label={`Печать PL ${shipment.number}`} title={shipment.pl_number}
                  disabled={Boolean(busy)} onClick={() => void printDocument(shipment, "pl")}>PL</button>
                <button type="button" className="link-button intake-doc-action" aria-label={`Печать DN ${shipment.number}`} disabled={Boolean(busy) || shipment.missing_data.length > 0}
                  title={shipment.missing_data.length ? `Не хватает: ${shipment.missing_data.join(", ")}` : shipment.dn_number}
                  onClick={() => void printDocument(shipment, "dn")}>DN</button>
              </> : <><span>PL</span><span>DN</span></>}
              {upd.href ? <a className="intake-doc-action" href={upd.href} target="_blank" rel="noreferrer">{upd.text}</a>
                : <button type="button" className="link-button intake-doc-action" aria-label={`Внести УПД ${shipment.number}`} title={upd.text}
                  onClick={() => setCard(shipment)}>{upd.text}</button>}
              <button type="button" className="link-button intake-doc-action muted" aria-label={`Документы ${shipment.number}`} title="Короба, этикетки и перевыпуск"
                onClick={() => setOpened({ kind: "docs", id: shipment.id })}>Короба…</button>
            </span>
            {(upd.tone === "danger" || upd.tone === "warn") && <span className={`tone-${upd.tone}`}>{upd.tone === "danger" ? "нет файла УПД" : "тип УПД не выбран"}</span>}</>}
      </span>;
    })}</span>;
  };
  /** «Отгружено»: число — кнопка окна отправок проекта; подсвечена, когда есть готовое к отгрузке. */
  const sentCell = (row: IntakeLine) => {
    if (!row.kbActions) return dash;
    if (tab === "shipped") return count(String(piecesOf(row).shipped));
    if (!canDispatch || !kbShips(row).length) return count(row.sent);
    const toShip = piecesOf(row).to_ship;
    return <button type="button" className={toShip > 0 ? "intake-take-btn" : "intake-num-btn"} title={toShip > 0 ? `Готово к отгрузке: ${toShip}` : "Отправки проекта"}
      aria-label={`Отгрузка ${who(row)}: отгружено ${num(row.sent)}${toShip > 0 ? `, к отгрузке ${toShip}` : ""}`}
      onClick={() => setOpened({ kind: "ship", project: row.project })}>{num(row.sent)}</button>;
  };

  const columns: DataColumn<IntakeLine>[] = [
    { key: "project", title: "Проект", value: (row) => row.project, hidden: true },
    { key: "kb", title: "КБ", value: (row) => row.item_index, render: (row) => <span className="intake-kb">{row.item_index}</span> },
    // Строка — одна линия (владелец 15.10): длинное наименование обрезается «…», целиком — в подсказке.
    { key: "name", title: "Наименование", value: (row) => row.name, render: (row) => <span className="intake-clip intake-name" title={row.name}>{row.name}</span> },
    { key: "needed", title: "Заказано", value: (row) => num(row.needed), numeric: true, render: (row) => row.needed },
    { key: "left", title: "Остаток", value: (row) => row.left, numeric: true, render: (row) => row.left > 0 ? row.left : dash },
    { key: "launched", title: "Запущено", value: (row) => num(row.launched), numeric: true, render: (row) => count(row.launched) },
    { key: "accepted", title: "Принято", value: (row) => num(row.accepted), numeric: true, render: (row) => count(row.accepted) },
    { key: "remaining", title: "Можно принять", value: (row) => row.act ? 0 : num(row.remaining), numeric: true,
      render: (row) => {
        if (row.act || !(num(row.remaining) > 0)) return dash;
        if (!canWarehouse) return <strong className="tone-ok" title={tasksText(row)}>{row.remaining}</strong>;
        return <button type="button" className="intake-take-btn" title={tasksText(row) || "Принять на склад"} aria-label={`Принять ${who(row)}: можно ${row.remaining}`}
          onClick={() => setIntake([source(row)])}>{row.remaining}</button>;
      } },
    { key: "packed", title: "Упаковано", value: (row) => num(row.packed), numeric: true, render: packCell },
    ...DELIVERY_COLUMNS.map((key): DataColumn<IntakeLine> => ({ key,
      title: ({ receiver: "Получатель", delivery: "Способ доставки", carrier: "Перевозчик", address: "Адрес", phone: "Телефон", warehouseNote: "Комментарий для склада" } as Record<string, string>)[key],
      value: (row) => deliveryOf(row).map((data) => deliveryText(row, key, data)).join("; "),
      wrap: true, minWidth: key === "address" || key === "warehouseNote" ? 120 : 84, maxWidth: key === "address" || key === "warehouseNote" ? 220 : 160,
      render: (row) => deliveryCell(row, key),
    })),
    { key: "track", title: "Трек", wrap: true, minWidth: 84, maxWidth: 140, value: (row) => shownShips(row).map((shipment) => shipment.track_number).filter(Boolean).join(", "),
      render: (row) => trackCell(row, tab === "packed" || tab === "all") },
    { key: "docs", title: "Документы", wrap: true, minWidth: 100, maxWidth: 180, value: (row) => docsShips(row).map((shipment) => shipment.pl_number ? [shipment.pl_number, shipment.dn_number, updState(shipment).text].join(" · ") : "не сформированы").join(", "), render: docsCell },
    { key: "ship", title: "Отгрузка", value: (row) => readyShips(row).length, render: (row) => {
      if (!row.kbActions || !canDispatch || !readyShips(row).length) return dash;
      return <button type="button" className="primary small intake-print" title={`Готово к отгрузке: ${piecesOf(row).to_ship}`} aria-label={`Отгрузить ${who(row)}`}
        onClick={() => setOpened({ kind: "ship", project: row.project })}>Отгрузить</button>;
    } },
    { key: "shippedAt", title: "Дата отгрузки", value: (row) => shippedAt(row), render: (row) => shippedAt(row) ? formatDay(shippedAt(row)) : dash },
    { key: "sent", title: "Отгружено", value: (row) => num(row.sent), numeric: true, render: sentCell },
    { key: "received", title: "Дата приёмки", value: (row) => row.act?.received_at ?? "", render: (row) => row.act ? formatDay(row.act.received_at) : dash },
    { key: "grn", title: "ГРН", wrap: true, minWidth: 132, maxWidth: 132, value: (row) => grnGroups(row).map((group) => group.grns.join(", ")).join("; "), render: (row) => {
      const groups = grnGroups(row);
      if (!groups.some((group) => group.id || group.grns.length)) return dash;
      const printable = canWarehouse && (row.act || num(row.accepted) > 0 || num(row.packed) > 0 || num(row.sent) > 0);
      return <span className="intake-grn">{groups.map((group, index) => <span className="intake-delivery-line" key={group.id}
        data-shipment={group.id || undefined} title={group.number || undefined}>
        {groups.length > 1 && <small className="muted intake-shipment-index">{index + 1}</small>}
        <span className="intake-grn">{group.grns.length ? group.grns.map((grn) => printable
          ? <button key={grn} type="button" className="link-button goods-grn intake-grn-link" title="Печать этикетки" aria-label={`Печать этикетки ${grn}`}
            disabled={Boolean(label?.busy)} aria-busy={label?.busy && label.grn === grn} onClick={() => void printLabel({ ...row, grn })}>{grn}</button>
          : <span className="goods-grn" key={grn}>{grn}</span>) : dash}</span>
      </span>)}</span>;
    } },
    // Задания — по умолчанию в подсказке «Можно принять» (колонку можно включить в «Колонках»): на 16" таблица без прокрутки вбок.
    { key: "tasks", title: "Задания", hidden: true, value: (row) => row.tasks.map((task) => task.number).join(", "),
      render: (row) => row.tasks.length ? <span className="intake-clip intake-stage muted" title={tasksText(row)}>{row.tasks.map((task) => task.number).join(", ")}</span> : dash },
  ];
  // Колонка, где у всех строк вкладки пусто, не показывается (владелец 15.10: «почти все числа — прочерки»).
  const OPTIONAL: Record<string, (row: IntakeLine) => boolean> = {
    launched: (row) => num(row.launched) > 0, accepted: (row) => num(row.accepted) > 0,
    remaining: (row) => num(row.remaining) > 0,
    packed: (row) => row.kbActions && (num(row.packed) > 0 || packable(source(row)) > 0 || kbShips(row).some((shipment) => ACTIVE.includes(shipment.status))),
    track: (row) => row.kbActions && (trackShips(row).length > 0 || kbShips(row).some((shipment) => Boolean(shipment.track_number))),
    sent: (row) => row.kbActions && (num(row.sent) > 0 || kbShips(row).length > 0),
    grn: (row) => Boolean(row.grn), received: (row) => Boolean(row.act), tasks: (row) => row.tasks.length > 0,
  };
  const visibleColumns = tab === "all"
    ? columns.filter((column) => !["ship", "shippedAt", "docs"].includes(column.key) && (!OPTIONAL[column.key] || shown.some(OPTIONAL[column.key])))
    : [...TAB_COLUMNS[tab], ...(!["packed", "to_ship", "shipped"].includes(tab) ? DELIVERY_COLUMNS : [])].flatMap((key) => columns.filter((column) => column.key === key))
      .map((column) => column.key === "tasks" ? { ...column, hidden: false } : column);

  const groupLabel = (project: string, groupRows: IntakeLine[]) => {
    const first = head[project];
    const title = [project, first?.customer].filter(Boolean).join(" · ");
    const ready = groupRows.filter((row) => !row.act && num(row.remaining) > 0);
    const total = round2(ready.reduce((sum, row) => sum + num(row.remaining), 0));
    return <span className="intake-group"><span className="intake-group-title">{title}</span>
      {canWarehouse && (tab === "ready" || tab === "production" || tab === "all") && ready.length > 1 && <button type="button" className="primary small" onClick={(event) => { event.stopPropagation(); setIntake(ready.map(source)); }}>
        Принять всё: {ready.length} КБ · {total} шт</button>}
      </span>;
  };

  const projectRows = (project: string) => rows.filter((row) => row.project === project);
  const projectShips = (project: string) => shipments.filter((shipment) => ofProject(shipment, project));
  const changeTab = (key: Tab) => { tabChosen.current = true; setTab(key); setNotice(""); };

  return <section className="page intake-page">
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
        <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : undefined} onClick={() => changeTab(key)}>
          {text}<span className="page-count"> {allRows.filter((row) => inTab(row, key)).length}</span></button>)}</div>
      <DataTable key={tab} label="Приёмка с производства" storageKey={tab === "all" ? "production-intake" : `production-intake:${tab}`} rows={shown} columns={visibleColumns} rowKey={lineKey}
        groupBy={(row) => row.project} groupLabel={groupLabel} exportName="Приёмка с производства"
        searchPlaceholder="Проект, КБ, наименование или задание"
        rowClassName={(row) => row.act ? "intake-act-row" : tab === "all" && !DISPATCH_STAGES.some(([stage]) => hasWork(stage, row, piecesOf(row))) ? "ship-line-off" : ""}
        emptyText={allRows.length ? EMPTY[tab] : EMPTY.all} />
      {label && <div className="intake-print-status"><strong>Этикетка {label.grn}</strong><PrintOutcome result={label.result} error={label.error} /></div>}
      {canWarehouse && boxes.length > 0 && <FinishedGoodsHandoff boxes={boxes} goods={goods} busy={busy} mutate={mutateBoxes} />}
    </>}
    {intake && <IntakeWindow rows={intake} close={() => setIntake(null)} onDone={accepted} />}
    {opened?.kind === "pack" && <PackWindow project={opened.project} rows={projectRows(opened.project)} shipments={projectShips(opened.project)} rights={rights} reload={reload} close={() => setOpened(null)} />}
    {opened?.kind === "ship" && <ShipWindow project={opened.project} shipments={projectShips(opened.project)} rights={rights} reload={reload} close={() => setOpened(null)} />}
    {opened?.kind === "docs" && (() => {
      const shipment = shipments.find((item) => item.id === opened.id);
      return shipment ? <ShipmentDocsWindow key={shipment.id} shipment={shipment} rights={rights} reload={reload} close={() => setOpened(null)} /> : null;
    })()}
    {opened?.kind === "track" && <TrackWindow title={who(opened.row)} shipments={trackShips(opened.row)} reload={reload} close={() => setOpened(null)} />}
    {opened?.kind === "noupd" && <NoUpdWindow shipment={opened.shipment} mutate={(key, operation, message) => mutateBoxes(key, operation, typeof message === "string" ? message : "Готово")} close={() => setOpened(null)} />}
    {header === "journal" && <JournalWindow shipments={shipments} close={() => setHeader(null)} />}
    {header === "good" && <RegisterGoodWindow reload={reload} close={() => setHeader(null)} />}
    {header === "request" && <CreateShipmentWindow reload={reload} close={() => setHeader(null)} />}
    {card && <ShipmentDataWindow shipment={card} canEdit={canDispatch} onSaved={reload} close={() => setCard(null)} />}
  </section>;
}
