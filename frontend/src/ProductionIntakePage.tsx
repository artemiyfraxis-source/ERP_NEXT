import { useEffect, useMemo, useRef, useState } from "react";
import { failureText, shippingApi, type Principal, type ProjectAddressee } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { ShipmentDataWindow } from "./ShipmentData";
import { DispatchCard } from "./DispatchCard";
import { DISPATCH_STAGES, stageLabel, type DispatchStage } from "./dispatchStage";
import { formatDay, formatNumber, NBSP } from "./format";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";
import { dataRows } from "./dispatchPositionRows";
import { canPack, COUNTER_SCOPE, dispatchBoard, hasWork, kbKey, num, ofKb, ofProject, packable, permittedPack, recentShipment, round2, SHIPPED_DAYS, stagePieces, type Pieces, type Tab } from "./intakeStages";
import { isPickup, termsText } from "./carriers";
import { canAct, canSee, hasFullAccess } from "./navigation";
import { intakeApi, type DispatchShipment, type IntakeAct, type IntakeRow } from "./productionIntakeApi";
import { IntakeWindow } from "./IntakeWindow";
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
import "./shipping.css";

/* «Приёмка с производства» (владелец 14.10, 29.10.2026): всё, что в работе, по проектам — и приёмка, и упаковка, и
   отгрузка на одном экране, экран «Отгрузка» убран. Вкладки — шаги пути готовой продукции; КБ, у которой штуки сразу в
   нескольких шагах, видна в каждом. Как в приёмке: число в колонке — кнопка, действие — во всплывающем окне.
   «Можно принять» — окно приёмки; «Упаковано» — упаковка (после решения менеджера «упаковывать»), PL/DN, короба,
   этикетки; «Трек…» — трек ТК, когда есть УПД и ТК; «Отгружено» — отправки проекта, «Отгрузить», без УПД — по ПИН. */

const TABS: ReadonlyArray<readonly [Tab, string]> = [...DISPATCH_STAGES, ["all", "Все"]];
/** Старые ссылки `?section=shipping&tab=…` и вкладки бывшей «Отгрузки» ведут в свои шаги. */
const TAB_ALIASES: Record<string, Tab> = { stock: "accepted", pack: "accepted", shipments: "packed", ship: "to_ship", done: "shipped", production: "production" };
/** Акты приёмки (зелёные строки с ГРН) — там, где о приёмке речь. */
const DELIVERY_COLUMNS = ["receiver", "delivery", "carrier", "address", "phone", "warehouseNote"] as const;
const POSITION_COLUMNS = ["project", "kb", "name", "quantity", "status", "grn", "shipmentStatus", "shippedAt", "pl", "dn", "upd"];
const STAGE_TONE: Record<DispatchStage, Tone> = { waiting: "neutral", production: "info", ready: "info", accepted: "warning", packed: "warning", to_ship: "info", shipped: "success" };
/** Колонки и действие каждой вкладки (владелец 29.10: «зачем лишняя инфа, кнопки 2, приёмка в отгруженных»): шаг показывает
 *  только своё; полный набор — во «Все». «Проект» — скрытая колонка для поиска и группировки. */
const TAB_COLUMNS: Record<Exclude<Tab, "all">, string[]> = {
  waiting: POSITION_COLUMNS,
  // «В работе» принимается до заказанного и без «Готово» (владелец 28.10 и 29.10: «кладовщик не может принять без статуса»).
  // Кладовщику задания цеха не нужны (владелец 29.10: «зачем тут Алексею задания»): номера — в подсказке кнопки «Принять»
  // и в «Колонках».
  production: [...POSITION_COLUMNS, "remaining"],
  ready: [...POSITION_COLUMNS, "remaining"],
  accepted: [...POSITION_COLUMNS, "packed"],
  packed: [...POSITION_COLUMNS, "track", "boxes"],
  to_ship: [...POSITION_COLUMNS, "ship", "track", "boxes"],
  shipped: POSITION_COLUMNS,
};
const EMPTY: Record<Tab, string> = {
  waiting: "В ожидании производства ничего нет.", production: "В производстве ничего нет.", ready: "Готового к приёмке нет.",
  accepted: "Принятого и не упакованного нет.", packed: "Упакованного, но не готового к отгрузке нет.",
  to_ship: "Готового к отгрузке нет — нужны УПД, ТК и трек.", shipped: `Отгруженного за ${SHIPPED_DAYS} дней нет — история в «Журнале».`, all: "Нет КБ в работе",
};

const keyOf = (row: IntakeRow) => `${row.project}\u0000${row.item_index}`;
const lineKey = (row: IntakeLine) => row.positionId;
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

/** Итог и действия относятся ко всей КБ; акты показывают только фактический приход по ГРН. */
type IntakeLine = IntakeRow & { positionId: string; positionStage: DispatchStage | null; positionQuantity: number; act?: IntakeAct; receipt?: IntakeAct; left: number; kbActions: boolean; total?: boolean; shipment?: DispatchShipment };

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
  const [projectData, setProjectData] = useState<string | null>(null);

  async function reload() {
    try {
      const [intakeResult, shipmentResult, addresseeResult] = await Promise.all([
        intakeApi.rows(),
        intakeApi.shipments(),
        shippingApi.addressees(),
      ]);
      const loadedRows = intakeResult.rows ?? [];
      const loadedShipments = shipmentResult.shipments ?? [];
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

  const board = useMemo(() => dispatchBoard(rows, shipments), [rows, shipments]);
  const { rows: allRows, pieces } = board;
  const piecesOf = (row: IntakeRow) => pieces.get(kbKey(row)) ?? stagePieces(row, shipments);
  const shown = useMemo(() => dataRows(shipments, rows)
    .filter((position) => tab === "all" || position.stage === tab && (tab !== "shipped" || position.shipment && recentShipment(position.shipment)))
    .map((position): IntakeLine => {
      const source = allRows.find((row) => kbKey(row) === kbKey(position))!;
      return { ...source, name: position.name, unit: position.unit, grn: position.grn || null,
        positionId: position.id, positionStage: position.stage, positionQuantity: position.quantity,
        shipment: position.shipment ?? undefined, sent: position.shipment ? String(position.quantity) : source.sent,
        packed: position.shipment ? String(position.quantity) : source.packed, kbActions: true, left: 0,
        remaining: !position.shipment && (position.stage === "production" || position.stage === "ready") ? source.remaining : "0",
        receipt: position.stage === "accepted" && source.acts?.length === 1 ? source.acts[0] : undefined,
      };
    }),
  [allRows, rows, shipments, tab]);
  const head = useMemo(() => {
    const byProject: Record<string, IntakeRow> = {};
    for (const row of allRows) if (!byProject[row.project]) byProject[row.project] = row;
    return byProject;
  }, [allRows]);
  const live = (shipment: DispatchShipment) => shipment.status !== "Отменена";
  const kbShips = (row: IntakeRow) => shipments.filter((shipment) => live(shipment) && ofKb(shipment, row));
  const addresseeByProject = useMemo(() => new Map(addressees.map((card) => [card.project.trim().toUpperCase(), card])), [addressees]);
  const cardOf = (row: IntakeRow) => addresseeByProject.get(row.project.trim().toUpperCase());
  const shownShips = (row: IntakeRow & { shipment?: DispatchShipment }) => row.shipment ? [row.shipment] : [];
  const grnGroups = (row: IntakeLine) => {
    return [{ id: row.shipment?.id ?? "", number: row.shipment?.number ?? "", grns: row.grn ? row.grn.split(", ") : [] }];
  };
  const deliveryOf = (row: IntakeLine) => {
    const ships = shownShips(row);
    return ships.length ? ships : [cardOf(row)].filter((card): card is ProjectAddressee => Boolean(card));
  };
  const contactText = (row: IntakeLine, value: string) => row.shipment && value.trim().toLowerCase() === "самовывоз" ? "" : value;
  const deliveryText = (row: IntakeLine, field: typeof DELIVERY_COLUMNS[number], data: ProjectAddressee | DispatchShipment): string => {
    if (field === "receiver") return [...new Set([data.receiver, data.contact].map(value => contactText(row, value)).filter(Boolean))].join(" · ");
    if (field === "delivery") return isPickup(data.carrier) ? "Самовывоз" : termsText(data.terms);
    if (field === "carrier") return data.carrier;
    if (field === "address") return row.shipment ? contactText(row, data.shipping_point) : isPickup(data.carrier) ? "Не требуется" : data.shipping_point;
    if (field === "phone") return contactText(row, data.phone);
    return [...new Set([row.shipment ? "" : cardOf(row)?.note, data.note, "tk_note" in data ? data.tk_note : ""].map((text) => text?.trim()).filter(Boolean))].join("\n");
  };
  const deliveryCell = (row: IntakeLine, field: typeof DELIVERY_COLUMNS[number]) => {
    if (row.act) return dash;
    const entries = deliveryOf(row);
    return <span className="intake-delivery-list">{entries.length ? entries.map((data, index) => {
      const text = deliveryText(row, field, data);
      return <span className="intake-delivery-line" key={"id" in data ? data.id : data.project} title={"number" in data ? data.number : undefined}
        data-shipment={"id" in data ? data.id : undefined}>
        {entries.length > 1 && <small className="muted intake-shipment-index">{index + 1}</small>}
        {field === "receiver" ? row.shipment ? <span>{text || "—"}</span> : <span><span>{data.receiver || "—"}</span>{data.contact && <small className="muted intake-contact">{data.contact}</small>}</span>
          : field === "warehouseNote" ? <span>{text ? text.split("\n").map((note, line) => <span className="intake-note-line" key={line}>{note}</span>) : dash}</span>
            : <span>{text || "—"}</span>}
      </span>;
    }) : dash}</span>;
  };
  const readyShips = (row: IntakeRow & { shipment?: DispatchShipment }) => (row.shipment ? [row.shipment] : kbShips(row)).filter((shipment) => shipment.status === "Упакована" && shipment.ready_to_ship);
  const trackShips = (row: IntakeRow & { shipment?: DispatchShipment }) => (row.shipment ? [row.shipment] : kbShips(row)).filter((shipment) => shipment.status === "Упакована" && needsTrack(shipment));

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
    if (tab === "all" && line.positionStage !== "accepted") return dash;
    // «Упаковано» и «На отгрузку»: только число штук своего шага, действие — в соседней колонке.
    if (tab === "packed" || tab === "to_ship") return count(line.shipment ? line.packed : String(piecesOf(line)[tab]));
    if (!canDispatch) return tab === "accepted" ? dash : count(line.packed);
    const row = source(line);
    const avail = packable(row);
    const open = kbShips(row).some((shipment) => ACTIVE.includes(shipment.status));
    const waitDecision = avail > 0 && !canPack(row);
    const openPack = () => setOpened({ kind: "pack", project: row.project });
    if (tab === "accepted" || tab === "all") return <span className="intake-pack-actions">
        {canPack(row) && <button type="button" className="intake-take-btn" title={`Можно упаковать: ${permittedPack(row)}; не набрано: ${avail}`}
          aria-label={`Упаковать ${who(row)}: упаковано ${num(row.packed)}, можно ${permittedPack(row)}`} onClick={openPack}>Упаковать · {permittedPack(row)}</button>}
        {waitDecision && <span className="tone-warn">Нужно разрешение</span>}
        {open && <button type="button" className="link-button" aria-label={`Упаковка ${who(row)}: упаковано ${num(row.packed)}`} onClick={openPack}>Открыть упаковку</button>}
        {!canPack(row) && !waitDecision && !open && dash}
      </span>;
    if (canPack(row)) return <button type="button" className="intake-take-btn" title={`Можно упаковать: ${permittedPack(row)}; на складе: ${avail}`}
      aria-label={`Упаковать ${who(row)}: упаковано ${num(row.packed)}, можно ${permittedPack(row)}`} onClick={openPack}>{line.total ? <>Упаковать остаток · {permittedPack(row)}</> : num(row.packed)}</button>;
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
      <span>{contactText(row, shipment.track_number) || (!needsTrack(shipment) ? "Не требуется" : "—")}</span></span>)}</span> : null;
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
        aria-label={`Трек ${who(row)}`} onClick={() => setOpened({ kind: "track", row })}>Трек…</button></span>;
  };
  /** Каждая отправка держит свои PL, DN и УПД; формирование и перевыпуск остаются явными действиями. */
  const docsShips = (row: IntakeLine) => row.kbActions ? shownShips(row).filter((shipment) => row.shipment || shipment.status === "Упакована") : [];
  const docsCell = (row: IntakeLine, kind: "pl" | "dn" | "upd" | "boxes") => {
    if (kind === "upd" && row.positionStage === "accepted" && !row.shipment && row.kbActions) {
      const projectCard = cardOf(row);
      return <button type="button" className="link-button intake-doc-action" aria-label={`Данные отправки ${row.project}`} onClick={() => setProjectData(row.project)}>
        {projectCard?.upd_type === "БЕЗ УПД" ? "Без УПД" : <>{projectCard?.upd_number ? `УПД № ${projectCard.upd_number}` : "Внести УПД"}
          {projectCard?.upd_url ? " · файл" : " · без файла"}</>}
      </button>;
    }
    const ships = docsShips(row);
    if (!ships.length) return dash;
    return <span className="intake-docs">{ships.map((shipment, index) => {
      const upd = updState(shipment);
      const number = kind === "pl" ? shipment.pl_number : shipment.dn_number;
      return <span className="intake-docs-line" key={shipment.id} data-shipment={shipment.id}>
        {ships.length > 1 && <span className="intake-ship-no muted">{index + 1} · {shipment.number}</span>}
        {kind === "pl" || kind === "dn"
          ? !number ? <span className="muted">не сформирован</span>
            : canPrintDocs ? <button type="button" className="link-button intake-doc-action"
              aria-label={`Печать ${kind.toUpperCase()} ${shipment.number}`}
              title={kind === "dn" && ACTIVE.includes(shipment.status) && shipment.missing_data.length ? `Не хватает: ${shipment.missing_data.join(", ")}` : number}
              disabled={Boolean(busy) || (kind === "dn" && ACTIVE.includes(shipment.status) && shipment.missing_data.length > 0)}
              onClick={() => void printDocument(shipment, kind)}>{kind.toUpperCase()}</button>
            : <span title={number}>{kind.toUpperCase()}</span>
          : kind === "upd" ? <>
            {upd.href ? <a className="intake-doc-action" href={upd.href} target="_blank" rel="noreferrer">{upd.text}</a>
              : <button type="button" className="link-button intake-doc-action" aria-label={`Внести УПД ${shipment.number}`} title={upd.text}
                onClick={() => setCard(shipment)}>{upd.text}</button>}
            {(upd.tone === "danger" || upd.tone === "warn") && <span className={`tone-${upd.tone}`}>{upd.tone === "danger" ? "нет файла УПД" : "тип УПД не выбран"}</span>}
          </> : !shipment.pl_number ? (rights.canEdit
            ? <button type="button" className="secondary small intake-print" aria-label={`Сформировать отгрузочные ${shipment.number}`}
              onClick={() => setOpened({ kind: "pack", project: row.project })}>Сформировать отгрузочные</button>
            : <span className="muted">не сформированы</span>)
            : <button type="button" className="link-button intake-doc-action muted" aria-label={`Документы ${shipment.number}`} title="Короба, этикетки и перевыпуск"
              onClick={() => setOpened({ kind: "docs", id: shipment.id })}>Короба…</button>}
      </span>;
    })}</span>;
  };
  /** «Отгружено»: число — кнопка окна отправок проекта; подсвечена, когда есть готовое к отгрузке. */
  const sentCell = (row: IntakeLine) => {
    if (!row.kbActions) return dash;
    if (tab === "shipped") return count(row.sent);
    if (!canDispatch || !kbShips(row).length) return count(row.sent);
    const toShip = piecesOf(row).to_ship;
    return <button type="button" className={toShip > 0 ? "intake-take-btn" : "intake-num-btn"} title={toShip > 0 ? `Готово к отгрузке: ${toShip}` : "Отправки проекта"}
      aria-label={`Отгрузка ${who(row)}: отгружено ${num(row.sent)}${toShip > 0 ? `, к отгрузке ${toShip}` : ""}`}
      onClick={() => setOpened({ kind: "ship", project: row.project })}>{num(row.sent)}</button>;
  };

  const columns: DataColumn<IntakeLine>[] = [
    { key: "project", title: "Проект", value: (row) => row.project, hidden: true },
    { key: "kb", title: "КБ", width: 56, maxWidth: 72, value: (row) => row.item_index, render: (row) => <span className="intake-kb">{row.item_index}</span> },
    // Строка — одна линия (владелец 15.10): длинное наименование обрезается «…», целиком — в подсказке.
    { key: "name", title: "Наименование", grow: true, minWidth: 160, wrap: true, value: (row) => row.name, render: (row) => <span className="ship-position-name" title={row.name}>{row.total && <strong>Итого по КБ<br /></strong>}{row.name}</span> },
    { key: "quantity", title: "Количество", width: 88, numeric: true, value: (row) => row.positionQuantity,
      render: (row) => `${formatNumber(row.positionQuantity)}${NBSP}${row.unit}` },
    { key: "status", title: "Этап позиции", width: 112, value: (row) => row.positionStage === null && row.shipment?.status !== "Отменена" ? "Не набрано" : stageLabel(row.positionStage),
      render: (row) => <StatusChip tone={row.positionStage ? STAGE_TONE[row.positionStage] : "neutral"}>{row.positionStage === null && row.shipment?.status !== "Отменена" ? "Не набрано" : stageLabel(row.positionStage)}</StatusChip> },
    { key: "shipmentStatus", title: "Статус отправки", width: 130, maxWidth: 160, wrap: true,
      value: (row) => row.shipment ? `${row.shipment.status} · ${row.shipment.number}` : "Не создана",
      render: (row) => <span className="ship-position-shipment"><span>{row.shipment?.status ?? "Не создана"}</span>{row.shipment &&
        <button type="button" className="link-button ship-sub" aria-label={`Данные отправки ${row.shipment.number}`} onClick={() => setCard(row.shipment!)}>{row.shipment.number}</button>}</span> },
    { key: "needed", title: "Заказано", value: (row) => num(row.needed), numeric: true, render: (row) => row.act ? dash : row.needed },
    { key: "left", title: "Остаток", value: (row) => row.left, numeric: true, render: (row) => row.left > 0 ? row.left : dash },
    { key: "launched", title: "Запущено", value: (row) => num(row.launched), numeric: true, render: (row) => count(row.launched) },
    { key: "accepted", title: tab === "accepted" ? "К упаковке" : "Принято", value: (row) => tab === "accepted" ? piecesOf(row).accepted : num(row.accepted), numeric: true,
      render: (row) => count(String(tab === "accepted" ? piecesOf(row).accepted : row.accepted)) },
    { key: "remaining", title: "Можно принять", value: (row) => row.act ? 0 : num(row.remaining), numeric: true,
      render: (row) => {
        if (row.act || !(num(row.remaining) > 0)) return dash;
        if (!canWarehouse) return <strong className="tone-ok" title={tasksText(row)}>{row.remaining}</strong>;
        return <button type="button" className="intake-take-btn" title={tasksText(row) || "Принять на склад"} aria-label={`Принять ${who(row)}: можно ${row.remaining}`}
          onClick={() => setIntake([source(row)])}>{row.remaining}</button>;
      } },
    { key: "packed", title: tab === "accepted" || tab === "all" ? "Упаковать" : "Упаковано", value: (row) => row.positionStage === "accepted" ? permittedPack(source(row)) : num(row.packed), numeric: false, render: (row) => row.total ? count(row.packed) : packCell(row) },
    { key: "free", title: "Не упаковано", value: (row) => row.kbActions ? packable(source(row)) : 0, numeric: true,
      render: (row) => row.kbActions ? <>{packable(source(row))}{row.total && <div>{packCell(row)}</div>}</> : dash },
    ...DELIVERY_COLUMNS.map((key): DataColumn<IntakeLine> => ({ key,
      title: ({ receiver: "Получатель", delivery: "Способ доставки", carrier: "Перевозчик", address: "Адрес", phone: "Телефон", warehouseNote: "Комментарий для склада" } as Record<string, string>)[key],
      value: (row) => deliveryOf(row).map((data) => deliveryText(row, key, data)).join("; "),
      wrap: true, minWidth: key === "address" || key === "warehouseNote" ? 120 : 84, maxWidth: key === "address" || key === "warehouseNote" ? 220 : 160,
      render: (row) => deliveryCell(row, key),
    })),
    { key: "track", title: "Трек", wrap: true, minWidth: 84, maxWidth: 140, value: (row) => shownShips(row).map((shipment) => contactText(row, shipment.track_number)).filter(Boolean).join(", "),
      render: (row) => trackCell(row, tab === "packed" || tab === "all") },
    ...(["pl", "dn", "upd", "boxes"] as const).map((kind): DataColumn<IntakeLine> => ({
      key: kind, title: { pl: "PL", dn: "DN", upd: "УПД", boxes: "Короба" }[kind], wrap: true,
      width: kind === "pl" ? 108 : kind === "dn" ? 112 : 96, maxWidth: kind === "upd" ? 120 : 132,
      value: (row) => docsShips(row).map((shipment) => kind === "upd" ? updState(shipment).text
        : kind === "boxes" ? (shipment.pl_number ? "Короба" : "не сформированы")
        : (kind === "pl" ? shipment.pl_number : shipment.dn_number) || "не сформирован").join(", "),
      render: (row) => docsCell(row, kind),
    })),
    { key: "ship", title: "Отгрузка", value: (row) => readyShips(row).length, render: (row) => {
      if (tab === "all" && row.shipment && canDispatch) return <button type="button" className="secondary small"
        aria-label={`Отправка ${row.shipment.number}`} onClick={() => setOpened({ kind: "ship", project: row.project })}>Отправка…</button>;
      if (row.positionStage !== "to_ship" || !row.kbActions || !canDispatch || !readyShips(row).length) return dash;
      return <button type="button" className="primary small intake-print" title={`Готово к отгрузке: ${piecesOf(row).to_ship}`} aria-label={`Отгрузить ${who(row)}`}
        onClick={() => setOpened({ kind: "ship", project: row.project })}>Отгрузить</button>;
    } },
    { key: "shippedAt", title: "Дата отгрузки", width: 104, value: (row) => row.shipment?.shipped_at ?? "",
      render: (row) => row.shipment?.shipped_at ? formatDay(row.shipment.shipped_at) : dash },
    { key: "sent", title: "Отгружено", value: (row) => num(row.sent), numeric: true, render: sentCell },
    { key: "received", title: "Дата приёмки", value: (row) => (row.act ?? row.receipt)?.received_at ?? "", render: (row) => (row.act ?? row.receipt) ? formatDay((row.act ?? row.receipt)!.received_at) : dash },
    { key: "grn", title: "ГРН", wrap: true, width: 128, maxWidth: 132, value: (row) => grnGroups(row).map((group) => group.grns.join(", ")).join("; "), render: (row) => {
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
  const primaryColumns = tab === "all" ? [...POSITION_COLUMNS, "remaining", "packed", "ship", "track", "boxes"] : TAB_COLUMNS[tab];
  const visibleColumns = [...primaryColumns, ...DELIVERY_COLUMNS, ...(!primaryColumns.includes("track") ? ["track"] : [])]
    .flatMap((key) => columns.filter((column) => column.key === key)).map((column) => ({ ...column, hidden: column.hidden || !primaryColumns.includes(column.key) }));

  const groupLabel = (project: string, groupRows: IntakeLine[]) => {
    const first = head[project];
    const title = [project, first?.customer].filter(Boolean).join(" · ");
    const ready = [...new Map(groupRows.filter((row) => !row.act && num(row.remaining) > 0).map((row) => [kbKey(row), row])).values()];
    const total = round2(ready.reduce((sum, row) => sum + num(row.remaining), 0));
    return <span className="ship-position-group"><span className="ship-position-group-title">{title}</span>
      {tab === "accepted" && <button type="button" className="secondary small" onClick={(event) => { event.stopPropagation(); setProjectData(project); }}>
        Данные отправки</button>}
      {canWarehouse && (tab === "ready" || tab === "production" || tab === "all") && ready.length > 1 && <button type="button" className="primary small" onClick={(event) => { event.stopPropagation(); setIntake(ready.map(source)); }}>
        Принять всё: {ready.length} КБ · {total} шт</button>}
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
        <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : undefined} onClick={() => changeTab(key)}>
          {text}<span className="page-count"> {board.counts[key]}</span></button>)}</div>
      <p className="muted" title="Части одной КБ могут находиться на разных этапах; во «Все» КБ считается один раз. Поиск и фильтры таблицы не меняют счётчики.">{COUNTER_SCOPE}{shipments.length >= 200 ? " · Загружены последние 200 отправок" : ""}</p>
      <DataTable key={tab} label="Приёмка с производства" storageKey={`production-intake:positions-v2:${tab}`} rows={shown} columns={visibleColumns} rowKey={lineKey}
        groupBy={(row) => row.project} groupLabel={groupLabel} exportName="Приёмка с производства"
        searchPlaceholder="Проект, КБ, наименование или задание"
        rowClassName={(row) => row.act ? "intake-act-row" : tab === "all" && !DISPATCH_STAGES.some(([stage]) => hasWork(stage, row, piecesOf(row))) ? "ship-line-off" : ""}
        emptyText={allRows.length ? EMPTY[tab] : EMPTY.all} />
      {label && <div className="intake-print-status"><strong>Этикетка {label.grn}</strong><PrintOutcome result={label.result} error={label.error} /></div>}
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
    {card && <ShipmentDataWindow shipment={card} canEdit={canDispatch} canCorrectHistory={hasFullAccess(principal) && !principal.viewing} onSaved={reload} close={() => setCard(null)} />}
    {projectData && <DispatchCard project={projectData} customer={head[projectData]?.customer} stage="accepted" canEdit={canDispatch} onSaved={() => void reload()} close={() => setProjectData(null)} />}
  </section>;
}
