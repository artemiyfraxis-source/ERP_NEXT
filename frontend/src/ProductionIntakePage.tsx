import { useEffect, useMemo, useRef, useState } from "react";
import { failureText, productionApi, shippingApi, type FinishedGood, type FinishedGoodsReadyBox, type Principal } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { DispatchCard } from "./DispatchCard";
import { DISPATCH_STAGES } from "./dispatchStage";
import { FinishedGoodsHandoff } from "./FinishedGoodsHandoff";
import { formatDay, formatDayMonth } from "./format";
import { canPack, hasWork, kbKey, num, ofKb, ofProject, packable, round2, SHIPPED_DAYS, shippedOnlyRows, stagePieces, type Pieces, type Tab } from "./intakeStages";
import { daysUntil } from "./productionGraph";
import { canAct, canSee, hasFullAccess } from "./navigation";
import { intakeApi, type DispatchShipment, type IntakeAct, type IntakeRow } from "./productionIntakeApi";
import { IntakeWindow, leftOf } from "./IntakeWindow";
import { PrintOutcome, type PrintResult } from "./LabelPrint";
import { PackWindow } from "./PackWindow";
import { PrintSetupButton } from "./PrintSetupWindow";
import { ACTIVE, needsTrack, NoUpdWindow, onlyUpdBlocks, readyForTrack, type CardRights } from "./ShipmentCard";
import { CreateShipmentWindow, JournalWindow, RegisterGoodWindow } from "./ShipmentJournal";
import { ShipmentDocsWindow, docsLabel, updState } from "./ShipmentDocs";
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
  // «Документы» — формирование отгрузочных и печать PL/DN/УПД окном (владелец 29.10: «где формирование отгрузочных, где печать УПД»).
  packed: ["project", "kb", "name", "packed", "track", "docs"],
  to_ship: ["project", "kb", "name", "packed", "track", "docs", "ship"],
  shipped: ["project", "kb", "name", "sent", "track", "shippedAt"],
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

/** Акты приёмки КБ — зелёные строки с ГРН над строкой КБ; строка КБ — всегда (на ней числа-кнопки). «Принято» у неё —
 *  прочерк, когда акты показаны: принятое уже расписано по ГРН. */
type IntakeLine = IntakeRow & { act?: IntakeAct; left: number };
function expand(row: IntakeRow, withActs: boolean): IntakeLine[] {
  const acts = withActs ? row.acts ?? [] : [];
  const left = leftOf(row);
  const lines: IntakeLine[] = acts.map((act) => ({
    ...row, act, left: 0, stage: "", launched: "0", delivered: "0", accepted: act.quantity, remaining: "0", packed: "0", sent: "0",
    grn: act.grn, location: act.location, tasks: [],
  }));
  lines.push(acts.length ? { ...row, left, accepted: "0", grn: null, location: "" } : { ...row, left });
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
  const rights: CardRights = { canManage, canWarehouse, canEdit: canDispatch, canUnship: hasFullAccess(principal) };
  const fromUrl = useRef(urlTab());
  const [rows, setRows] = useState<IntakeRow[]>([]);
  const [shipments, setShipments] = useState<DispatchShipment[]>([]);
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
  const [card, setCard] = useState<string | null>(null);

  async function reload() {
    try {
      const [intakeResult, shipmentResult, goodsResult, boxesResult] = await Promise.all([
        intakeApi.rows(),
        intakeApi.shipments(),
        canWarehouse ? shippingApi.goods() : Promise.resolve({ goods: [] as FinishedGood[] }),
        canWarehouse ? productionApi.finishedGoodsReady() : Promise.resolve({ boxes: [] as FinishedGoodsReadyBox[] }),
      ]);
      const loadedRows = intakeResult.rows ?? [];
      const loadedShipments = shipmentResult.shipments ?? [];
      setRows(loadedRows); setShipments(loadedShipments); setGoods(goodsResult.goods ?? []); setBoxes(boxesResult.boxes ?? []); setError("");
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
  const shown = useMemo(() => allRows.filter((row) => inTab(row, tab)).flatMap((row) => expand(row, ACT_TABS.includes(tab))),
    [allRows, tab, pieces]); // eslint-disable-line react-hooks/exhaustive-deps
  const head = useMemo(() => {
    const byProject: Record<string, IntakeRow> = {};
    for (const row of allRows) if (!byProject[row.project]) byProject[row.project] = row;
    return byProject;
  }, [allRows]);
  const live = (shipment: DispatchShipment) => shipment.status !== "Отменена";
  const kbShips = (row: IntakeRow) => shipments.filter((shipment) => live(shipment) && ofKb(shipment, row));
  const readyShips = (row: IntakeRow) => kbShips(row).filter((shipment) => shipment.status === "Упакована" && shipment.ready_to_ship);
  const shippedAt = (row: IntakeRow) => kbShips(row).filter((shipment) => SHIPPED_STATUS.includes(shipment.status)).map((shipment) => String(shipment.shipped_at ?? "")).sort().pop() ?? "";
  const trackShips = (row: IntakeRow) => kbShips(row).filter((shipment) => shipment.status === "Упакована" && needsTrack(shipment));

  /** Строка КБ как пришла с сервера (у строки после актов «Принято» обнулено для таблицы, окну нужно настоящее). */
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
    if (line.act) return dash;
    // «Упаковано» и «На отгрузку»: только число штук своего шага, действие — в соседней колонке.
    if (tab === "packed" || tab === "to_ship") return count(String(piecesOf(line)[tab]));
    if (!canDispatch) return count(line.packed);
    // Строка КБ под актами показывает «Принято» прочерком — считать надо по настоящей строке.
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
    if (row.act) return dash;
    const needing = action ? trackShips(row) : [];
    const tracks = (needing.length ? needing : kbShips(row)).map((shipment) => shipment.track_number).filter(Boolean);
    const text = tracks.length ? <span className="intake-clip intake-tracks" title={tracks.join(", ")}>{tracks.join(", ")}</span> : null;
    if (!canDispatch || !action) return text ?? dash;
    // Одно действие на строку: «Трек…», когда можно; «Без УПД…», когда держит только УПД; иначе выключенный «Трек…» с причиной.
    const packedShips = kbShips(row).filter((shipment) => shipment.status === "Упакована");
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
  /** «Документы»: у упакованной отправки — «Сформировать отгрузочные» (окно упаковки) или одна кнопка окна с PL, DN, УПД;
   *  печатается только в окне. Несколько отправок КБ — по строке на каждую с № ОТГ. */
  const docsShips = (row: IntakeLine) => row.act ? [] : kbShips(row).filter((shipment) => shipment.status === "Упакована");
  const docsCell = (row: IntakeLine) => {
    const ships = docsShips(row);
    if (!ships.length) return dash;
    return <span className="intake-docs">{ships.map((shipment) => {
      const upd = updState(shipment);
      return <span className="intake-docs-line" key={shipment.id}>
        {ships.length > 1 && <span className="intake-ship-no muted">{shipment.number}</span>}
        {!shipment.pl_number
          ? (rights.canEdit
            ? <button type="button" className="secondary small intake-print" aria-label={`Сформировать отгрузочные ${shipment.number}`} onClick={() => setOpened({ kind: "pack", project: row.project })}>Сформировать отгрузочные</button>
            : <span className="muted">не сформированы</span>)
          : <>
            <button type="button" className="secondary small intake-print" aria-label={`Документы ${shipment.number}`} title={upd.text} onClick={() => setOpened({ kind: "docs", id: shipment.id })}>{docsLabel(shipment)}</button>
            {(upd.tone === "danger" || upd.tone === "warn") && <span className={`tone-${upd.tone}`}>{upd.tone === "danger" ? "нет файла УПД" : "тип УПД не выбран"}</span>}</>}
      </span>;
    })}</span>;
  };
  /** «Отгружено»: число — кнопка окна отправок проекта; подсвечена, когда есть готовое к отгрузке. */
  const sentCell = (row: IntakeLine) => {
    if (row.act) return dash;
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
    { key: "track", title: "Трек", value: (row) => kbShips(row).map((shipment) => shipment.track_number).filter(Boolean).join(", "),
      render: (row) => trackCell(row, tab === "packed" || tab === "all") },
    { key: "docs", title: "Документы", value: (row) => docsShips(row).map((shipment) => shipment.pl_number ? docsLabel(shipment) : "не сформированы").join(", "), render: docsCell },
    { key: "ship", title: "Отгрузка", value: (row) => readyShips(row).length, render: (row) => {
      if (row.act || !canDispatch || !readyShips(row).length) return dash;
      return <button type="button" className="primary small intake-print" title={`Готово к отгрузке: ${piecesOf(row).to_ship}`} aria-label={`Отгрузить ${who(row)}`}
        onClick={() => setOpened({ kind: "ship", project: row.project })}>Отгрузить</button>;
    } },
    { key: "shippedAt", title: "Дата отгрузки", value: (row) => shippedAt(row), render: (row) => shippedAt(row) ? formatDay(shippedAt(row)) : dash },
    { key: "sent", title: "Отгружено", value: (row) => num(row.sent), numeric: true, render: sentCell },
    { key: "received", title: "Дата приёмки", value: (row) => row.act?.received_at ?? "", render: (row) => row.act ? formatDay(row.act.received_at) : dash },
    { key: "grn", title: "ГРН", value: (row) => row.grn ?? "", render: (row) => {
      if (!row.grn) return dash;
      const printable = canWarehouse && (row.act || num(row.accepted) > 0);
      return <span className="intake-grn"><span className="goods-grn">{row.grn}</span>
        {printable && <button type="button" className="secondary small intake-print" title="Печать этикетки" aria-label={`Печать этикетки ${row.grn}`}
          disabled={Boolean(label?.busy)} onClick={() => void printLabel(row)}>{label?.busy && label.grn === row.grn ? "…" : "Печать"}</button>}</span>;
    } },
    // Задания — по умолчанию в подсказке «Можно принять» (колонку можно включить в «Колонках»): на 16" таблица без прокрутки вбок.
    { key: "tasks", title: "Задания", hidden: true, value: (row) => row.tasks.map((task) => task.number).join(", "),
      render: (row) => row.tasks.length ? <span className="intake-clip intake-stage muted" title={tasksText(row)}>{row.tasks.map((task) => task.number).join(", ")}</span> : dash },
  ];
  // Колонка, где у всех строк вкладки пусто, не показывается (владелец 15.10: «почти все числа — прочерки»).
  const OPTIONAL: Record<string, (row: IntakeLine) => boolean> = {
    launched: (row) => num(row.launched) > 0, accepted: (row) => num(row.accepted) > 0,
    remaining: (row) => num(row.remaining) > 0,
    packed: (row) => !row.act && (num(row.packed) > 0 || packable(source(row)) > 0 || kbShips(row).some((shipment) => ACTIVE.includes(shipment.status))),
    track: (row) => !row.act && (trackShips(row).length > 0 || kbShips(row).some((shipment) => Boolean(shipment.track_number))),
    sent: (row) => !row.act && (num(row.sent) > 0 || kbShips(row).length > 0),
    grn: (row) => Boolean(row.grn), received: (row) => Boolean(row.act), tasks: (row) => row.tasks.length > 0,
  };
  const visibleColumns = tab === "all"
    ? columns.filter((column) => !["ship", "shippedAt", "docs"].includes(column.key) && (!OPTIONAL[column.key] || shown.some(OPTIONAL[column.key])))
    : TAB_COLUMNS[tab].flatMap((key) => columns.filter((column) => column.key === key)).map((column) => column.key === "tasks" ? { ...column, hidden: false } : column);

  const groupLabel = (project: string, groupRows: IntakeLine[]) => {
    const first = head[project];
    const title = [project, first?.customer].filter(Boolean).join(" · ");
    const left = first?.deadline ? daysUntil(first.deadline) : null;
    const ready = groupRows.filter((row) => !row.act && num(row.remaining) > 0);
    const total = round2(ready.reduce((sum, row) => sum + num(row.remaining), 0));
    return <span className="intake-group"><span className="intake-group-title">{title}</span>
      {first?.deadline && <span className={left !== null && left < 0 ? "intake-due tone-danger" : left !== null && left <= 3 ? "intake-due tone-warn" : "intake-due"}>
        срок {formatDayMonth(first.deadline)}{left !== null && left < 0 ? ` · просрочен на ${-left} дн.` : left === 0 ? " · сегодня" : ""}</span>}
      {canWarehouse && (tab === "ready" || tab === "production" || tab === "all") && ready.length > 1 && <button type="button" className="primary small" onClick={(event) => { event.stopPropagation(); setIntake(ready.map(source)); }}>
        Принять всё: {ready.length} КБ · {total} шт</button>}
      {canSee(principal, "shipping") && <button type="button" className="secondary small" onClick={(event) => { event.stopPropagation(); setCard(project); }}>Данные отправки</button>}</span>;
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
    {card && <DispatchCard project={card} customer={head[card]?.customer} canEdit={canDispatch} close={() => setCard(null)} />}
  </section>;
}
