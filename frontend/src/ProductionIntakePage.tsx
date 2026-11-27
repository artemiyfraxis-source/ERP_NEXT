import { useEffect, useMemo, useRef, useState } from "react";
import { failureText, shippingApi, type ItemAddressee, type Principal, type ProjectAddressee } from "./api";
import { DispatchCard } from "./DispatchCard";
import { ItemDeliveryWindow } from "./ProjectData";
import { formatNumber, NBSP, plural } from "./format";
import { actShares } from "./dispatchPositionRows";
import { dispatchBoard, kbKey, num, permittedPack, round2 } from "./intakeStages";
import { acceptable, acceptPlan, buildBoard, type BoardCard, type CardAction, type ColumnId } from "./intakeBoard";
import { IntakeBoard, type MenuItem } from "./IntakeBoardView";
import { focusOf, IntakeCardWindow, type FocusField } from "./IntakeCardWindow";
import { canAct, canSee, hasFullAccess, urlParam } from "./navigation";
import { deliveryGroups, intakeApi, type DispatchShipment, type IntakeAct, type IntakeRow as ApiIntakeRow } from "./productionIntakeApi";
import { IntakeWindow, RollbackActWindow } from "./IntakeWindow";
import { PrintOutcome, type PrintResult } from "./LabelPrint";
import { PackWindow } from "./PackWindow";
import { PrintSetupButton } from "./PrintSetupWindow";
import { NoUpdWindow, onlyUpdBlocks, ShipWithoutDataWindow, type CardRights, type Mutate } from "./ShipmentCard";
import { CreateShipmentWindow, JournalWindow, RegisterGoodWindow, useDocLink } from "./ShipmentJournal";
import { ShipWindow } from "./ShipWindow";
import { PackingPermissionWindow } from "./PackingPermissionWindow";
import { ShipmentDataWindow } from "./ShipmentData";
import { shipmentTitle } from "./shipmentTitle";
import "./productionIntake.css";
import "./shipping.css";

/* «Приёмка с производства» — доска по стадиям (владелец 27.11, вариант «В»): Производство → Принято → Упаковано →
   На отгрузку → Отгружено. Карточка — проект или отправка; главная кнопка двигает её дальше теми же запросами и
   проверками, что раньше, строка «⚠» говорит, чего не хватает и кто вносит. Клик по карточке — окно со строками по КБ
   и правкой данных на месте. Принимает, упаковывает и отгружает кладовщик («Отгрузка — склад»); менеджер («Отгрузка») —
   данные отправки, заявки, разрешение упаковки. Отгрузка уезжает целиком, по одной тем же `/ship` со своей версией. */

/** Старые ссылки `?tab=…` и вкладки бывшей «Отгрузки» подсвечивают свою колонку. */
const TAB_COLUMN: Record<string, ColumnId> = { waiting: "production", production: "production", ready: "production", accepted: "accepted", stock: "accepted", pack: "accepted",
  packed: "packed", shipments: "packed", to_ship: "to_ship", ship: "to_ship", shipped: "shipped", done: "shipped" };
type IntakeRow = ApiIntakeRow & { pack_auto_allowed?: boolean };
type Opened = { kind: "pack" | "ship"; project: string } | { kind: "noupd" | "bare"; shipment: DispatchShipment };
type RollingBack = { row: IntakeRow; act: IntakeAct; onStock: number };

const sumByUnit = (entries: Array<{ unit: string; quantity: number }>) => {
  const totals = new Map<string, number>();
  for (const entry of entries) totals.set(entry.unit, round2((totals.get(entry.unit) ?? 0) + entry.quantity));
  return [...totals].map(([unit, quantity]) => `${formatNumber(quantity)}${NBSP}${unit}`).join(", ");
};

export function ProductionIntakePage({ principal }: { principal: Principal }) {
  // Принимает, упаковывает и отгружает кладовщик («Отгрузка — склад»); менеджер («Отгрузка») — данные отправки, заявки.
  const canWarehouse = canAct(principal, "shipping_goods");
  const canManage = canAct(principal, "shipping");
  const canDispatch = canManage || canWarehouse;
  const rights: CardRights = { canManage, canWarehouse, canEdit: canDispatch, canUnship: hasFullAccess(principal) };
  // «Откатить приёмку» — тот же гейт, что у «Откатить отгрузку»: руководитель (администратор).
  const canRollback = hasFullAccess(principal);
  const linked = useRef(new URLSearchParams(window.location.search));
  const [rows, setRows] = useState<IntakeRow[]>([]);
  const [shipments, setShipments] = useState<DispatchShipment[]>([]);
  const [addressees, setAddressees] = useState<ProjectAddressee[]>([]);
  const [itemAddressees, setItemAddressees] = useState<Record<string, ItemAddressee[]>>({});
  const [deliveryEdit, setDeliveryEdit] = useState<{ project: string; items: ItemAddressee[] } | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [accepting, setAccepting] = useState<ReturnType<typeof acceptPlan> | null>(null);
  const [rollingBack, setRollingBack] = useState<RollingBack | null>(null);
  const [opened, setOpened] = useState<Opened | null>(null);
  // `?tab=journal` открывает только «Журнал»; `?shipment=` — окно отправки, а если её нет на доске (старше 30 дней, отменена) — «Журнал».
  const linkedShipment = useRef(linked.current.get("tab") === "journal" ? null : linked.current.get("shipment"));
  const [card, setCard] = useState<{ id: string; focus: FocusField | null } | null>(linkedShipment.current ? { id: `s:${linkedShipment.current}`, focus: null } : null);
  const [header, setHeader] = useState<"journal" | "good" | "request" | null>(linked.current.get("tab") === "journal" ? "journal" : null);
  const [label, setLabel] = useState<{ grn: string; result: PrintResult | null; error: string; busy: boolean } | null>(null);
  const [projectData, setProjectData] = useState<string | null>(null);
  const [permissionProject, setPermissionProject] = useState<string | null>(null);
  const [shipmentData, setShipmentData] = useState<string | null>(null);
  const [shipProgress, setShipProgress] = useState<{ done: number; total: number } | null>(null);
  const { error: docError, link: docLink } = useDocLink();
  /** Правка на месте вернула отправку: она заменяет прежнюю без перезагрузки всего списка. */
  const replaceShipment = (next: DispatchShipment) => setShipments((current) => current.map((item) => item.id === next.id ? { ...item, ...next } : item));

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
      const wanted = urlParam("shipment");
      if (wanted && !loadedShipments.some((shipment) => shipment.id === wanted)) loadedShipments.push(...(await intakeApi.shipments(wanted)).shipments);
      setRows(loadedRows); setShipments(loadedShipments); setError("");
      setAddressees(addresseeResult.addressees ?? []);
      setItemAddressees(deliveries);
    } catch (reason) {
      setError(failureText(reason, "Не удалось загрузить список КБ в работе"));
    } finally { setLoaded(true); }
  }
  useEffect(() => { void reload(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const board = useMemo(() => dispatchBoard(rows, shipments), [rows, shipments]);
  const cards = useMemo(() => buildBoard({ rows, shipments, addressees, rights: { canManage, canWarehouse, canDispatch } }),
    [rows, shipments, addressees, canManage, canWarehouse, canDispatch]);
  const head = useMemo(() => Object.fromEntries(board.rows.map((row) => [row.project, row] as const).reverse()), [board]);
  const open = card ? cards.find((item) => item.id === card.id) ?? null : null;
  useEffect(() => {
    if (!loaded || !linkedShipment.current) return;
    linkedShipment.current = null;
    if (!open) { setCard(null); setHeader("journal"); }
  }, [loaded]); // eslint-disable-line react-hooks/exhaustive-deps
  const projectRows = (project: string) => rows.filter((row) => row.project === project);
  const projectShips = (project: string) => shipments.filter((shipment) => shipment.lines.some((line) => line.project.trim().toUpperCase() === project.trim().toUpperCase()));
  const piecesReady = (kb: ApiIntakeRow) => board.pieces.get(kbKey(kb))?.ready ?? 0;

  /** «Печать» этикетки ГРН акта: та же очередь печати, что у приёмки закупки; статус — под доской. */
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
  async function rolledBack(message: string) { setRollingBack(null); setError(""); setNotice(message); await reload(); }
  async function accepted(pieceCount: number) { setAccepting(null); setError(""); setNotice(`Принято на склад: ${pieceCount} шт`); await reload(); }

  /** «Упаковать все разрешённые КБ» проекта: каждая КБ — на своё разрешение; по одной отправке на группу доставки, как в окне упаковки. */
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
    const failed: string[] = [];
    let packedKb = 0; let packedPieces = 0;
    for (const entries of buckets.values()) {
      try {
        const addressee = await intakeApi.dispatch(project);
        await shippingApi.projectShip({
          project, addressee_version: addressee.addressee?.version ?? 0, ship: false,
          lines: entries.map((entry) => ({ good_id: String(entry.kb.good_id), version: entry.kb.good_version ?? 0, quantity: String(entry.quantity) })),
          ...(canManage ? { documents: { upd_number: "", upd_date: null, deadline: null } } : {}),
        }, crypto.randomUUID());
        packedKb += entries.length; packedPieces += entries.reduce((sum, entry) => sum + entry.quantity, 0);
      } catch (reason) {
        failed.push(`${entries.map((entry) => entry.kb.item_index).join(", ")}: ${failureText(reason, "не упаковано")}`);
      }
    }
    if (packedKb) setNotice(`Упаковано ${packedKb} КБ · ${formatNumber(round2(packedPieces))} шт`);
    await reload();
    if (failed.length) setError(`Не упаковано ${list.length - packedKb} КБ — ${failed.join("; ")}`);
    setBusy("");
  }

  /** Позиции и штуки отправки целиком (с КБ других проектов, если отправка общая). */
  const shipmentSize = (shipment: DispatchShipment) => {
    const positions = shipment.lines.filter((line) => num(line.picked) > 0);
    return `${positions.length}${NBSP}поз. · ${sumByUnit(positions.map((line) => ({ unit: line.unit, quantity: num(line.picked) })))}`;
  };
  /** «Отгрузить» / «Отгрузить все готовые»: отправки уезжают целиком, по одной тем же `/ship` со своей версией; ход —
   *  «Отгружаем 1 из 3…», отказ одной не держит остальные и называет её проект. */
  async function shipShipments(ready: DispatchShipment[], scope = ""): Promise<boolean> {
    if (busy || !ready.length) return false;
    const count = `${ready.length} ${plural(ready.length, ["отправку", "отправки", "отправок"])}`;
    const others = ready.flatMap((shipment) => {
      const projects = [...new Set(shipment.lines.map((line) => line.project))];
      return projects.length > 1 ? [`${shipmentTitle(shipment)} уедет вместе с КБ проектов ${projects.join(", ")}`] : [];
    });
    const question = `Отгрузить ${count}${scope}: ${ready.map((shipment) => `${shipmentTitle(shipment)} (${shipmentSize(shipment)})`).join("; ")}?`
      + (others.length ? ` Отправка уезжает целиком: ${others.join("; ")}.` : "");
    if (!window.confirm(question)) return false;
    setBusy("bulk-ship"); setError(""); setNotice("");
    let shippedCount = 0; const failed: string[] = [];
    for (const [index, shipment] of ready.entries()) {
      setShipProgress({ done: index, total: ready.length });
      try { await shippingApi.action(shipment.id, "ship", shipment.version); shippedCount += 1; }
      catch (reason) { failed.push(`${shipmentTitle(shipment)}: ${failureText(reason, "не отгружена")}`); }
    }
    setShipProgress(null);
    if (shippedCount) setNotice(`Отгружено ${shippedCount} ${plural(shippedCount, ["отправка", "отправки", "отправок"])}`);
    await reload();
    if (failed.length) setError(`Не отгружено ${failed.length} — ${failed.join("; ")}`);
    setBusy("");
    return shippedCount > 0;
  }

  /** Окно приёмки только для переданных КБ: у сданных цехом — их штуки, у остальных поле пустое. */
  function acceptKbs(kbs: ApiIntakeRow[]) {
    const plan = acceptPlan(kbs, piecesReady);
    if (plan.rows.length) setAccepting(plan as typeof plan & { rows: IntakeRow[] });
  }
  const openCard = (target: BoardCard, focus: FocusField | null = null) => setCard({ id: target.id, focus });
  /** Главная кнопка карточки: те же запросы и проверки, что у прежних кнопок строк и отправок. */
  function act(target: BoardCard, action: CardAction) {
    const shipment = target.shipment;
    const title = shipment ? shipmentTitle(shipment) : target.project;
    switch (action.id) {
      case "accept": {
        acceptKbs(acceptable(target, piecesReady));
        return;
      }
      case "pack": setOpened({ kind: "pack", project: target.project }); return;
      case "permit": setPermissionProject(target.project); return;
      case "make-pl": if (shipment) void mutate(`pl:${shipment.id}`, () => shippingApi.issueDocument(shipment.id, shipment.version, "pl"), `PL сформирован: ${title}`); return;
      case "make-dn": if (shipment) void mutate(`dn:${shipment.id}`, () => shippingApi.issueDocument(shipment.id, shipment.version, "dn"), `DN сформирован: ${title}`); return;
      case "ship": if (shipment) void shipShipments([shipment]); return;
      default: openCard(target, focusOf(action, shipment));
    }
  }
  const menuOf = (target: BoardCard): MenuItem[] => {
    const items: MenuItem[] = target.extra.map((action) => ({ label: action.label, run: () => act(target, action) }));
    const shipment = target.shipment;
    const firstProject = target.project.split(" + ")[0];
    if (shipment) {
      if (canDispatch && ["Новая", "Собрана", "Упакована"].includes(shipment.status)) items.push({ label: shipment.status === "Упакована" ? "Изменить упаковку…" : "Открыть упаковку…", run: () => setOpened({ kind: "pack", project: firstProject }) });
      if (canDispatch && ["Отгружена", "Закрыта"].includes(shipment.status)) items.push({ label: "Действия…", run: () => setOpened({ kind: "ship", project: firstProject }) });
      if (target.column === "packed" || target.column === "accepted") {
        if (canWarehouse && onlyUpdBlocks(shipment) && (shipment.status === "Упакована" || Boolean(shipment.dn_number))) items.push({ label: "Отгрузить без УПД…", run: () => setOpened({ kind: "noupd", shipment }) });
        if (rights.canUnship && shipment.lines.some((line) => num(line.picked) > 0) && shipment.missing_ship.length > 0) items.push({ label: "Уже отгружена…", run: () => setOpened({ kind: "bare", shipment }) });
      }
      return items;
    }
    // Данные проекта видят и те, кто их не правит: окно только для чтения.
    items.push({ label: "Данные доставки проекта…", run: () => setProjectData(target.project) });
    if (canDispatch) {
      if (itemAddressees[target.project]?.length) items.push({ label: "Всем КБ проекта…", run: () => setDeliveryEdit({ project: target.project, items: itemAddressees[target.project] }) });
    }
    if (target.column === "accepted" && canDispatch) {
      const list = target.kbs.filter((kb) => permittedPack(kb) > 0 && kb.good_id).map((kb) => ({ kb: kb as IntakeRow, quantity: permittedPack(kb) }));
      if (list.length > 1) items.push({ label: `Упаковать все разрешённые КБ (${list.length})…`, run: () => void packAll(target.project, list, target.kbs.length - list.length) });
    }
    return items;
  };
  const columnTools = (column: ColumnId, shown: BoardCard[]) => {
    if (column !== "to_ship" || !canWarehouse) return null;
    const ready = shown.flatMap((item) => item.shipment ? [item.shipment] : []);
    return ready.length > 1 ? <div className="board-column-tools"><button type="button" className="secondary small" disabled={Boolean(busy)}
      onClick={() => void shipShipments(ready)}>Отгрузить все готовые · {ready.length}</button></div> : null;
  };
  const deliveryFor = (target: BoardCard) => {
    const project = target.shipment ? target.shipment.lines[0]?.project ?? target.project : target.project;
    const wanted = target.shipment ? new Set(target.shipment.lines.filter((line) => num(line.picked) > 0).map((line) => line.item_index)) : new Set(target.kbs.map((kb) => kb.item_index));
    const items = (itemAddressees[project] ?? []).filter((item) => wanted.has(item.item_index));
    if (items.length) setDeliveryEdit({ project, items });
  };
  const shipmentOpen = shipmentData ? shipments.find((shipment) => shipment.id === shipmentData) : undefined;
  const initialFilter = { query: linked.current.get("q") ?? "", gaps: linked.current.get("filter") === "gaps" };

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
    {shipProgress && <div className="warning-inline" role="status">Отгружаем {shipProgress.done + 1} из {shipProgress.total}…</div>}
    {!loaded ? <div className="empty">Загрузка…</div> : <>
      <IntakeBoard cards={cards} busy={Boolean(busy)} initial={initialFilter} picked={TAB_COLUMN[linked.current.get("tab") ?? ""] ?? null}
        onOpen={(target) => openCard(target)} onAction={act} menuOf={menuOf} columnTools={columnTools} onJournal={() => setHeader("journal")} />
      {label && <div className="intake-print-status"><strong>Этикетка {label.grn}</strong><PrintOutcome result={label.result} error={label.error} /></div>}
    </>}
    {open && <IntakeCardWindow key={open.id} card={open} focus={card?.focus ?? null} busy={Boolean(busy)} docLink={docLink}
      rights={{ canEdit: canDispatch, canHistory: rights.canUnship, canWarehouse, canRollback }}
      shares={(kb) => actShares(kb, shipments)} piecesReady={piecesReady} onAccept={acceptKbs} onAction={act} onShipment={replaceShipment} onHistory={(shipment) => setShipmentData(shipment.id)} onConflict={reload}
      onDelivery={deliveryFor} onRollback={(row, rolledAct, onStock) => setRollingBack({ row, act: rolledAct, onStock })} onPrintLabel={(grn) => void printLabel(grn)} close={() => setCard(null)} />}
    {accepting && <IntakeWindow rows={accepting.rows} quantities={accepting.quantities} blank={accepting.blank} close={() => setAccepting(null)} onDone={accepted} />}
    {rollingBack && <RollbackActWindow row={rollingBack.row} act={rollingBack.act} onStock={rollingBack.onStock} close={() => setRollingBack(null)} onDone={rolledBack} />}
    {opened?.kind === "pack" && <PackWindow project={opened.project} rows={projectRows(opened.project)} shipments={projectShips(opened.project)} rights={rights} reload={reload} close={() => setOpened(null)} />}
    {opened?.kind === "ship" && <ShipWindow project={opened.project} shipments={projectShips(opened.project)} rights={rights} reload={reload} close={() => setOpened(null)} />}
    {opened?.kind === "noupd" && <NoUpdWindow shipment={opened.shipment} mutate={mutate} close={() => setOpened(null)} />}
    {opened?.kind === "bare" && <ShipWithoutDataWindow shipment={opened.shipment} gaps={opened.shipment.missing_ship} mutate={mutate} close={() => setOpened(null)} />}
    {header === "journal" && <JournalWindow shipments={shipments} selectedShipment={shipments.find((shipment) => shipment.id === urlParam("shipment"))} initialQuery={urlParam("q")} close={() => setHeader(null)} />}
    {header === "good" && <RegisterGoodWindow reload={reload} close={() => setHeader(null)} />}
    {header === "request" && <CreateShipmentWindow reload={reload} close={() => setHeader(null)} />}
    {permissionProject && <PackingPermissionWindow project={permissionProject} editable={canManage} close={() => setPermissionProject(null)} saved={reload} />}
    {shipmentOpen && <ShipmentDataWindow key={shipmentOpen.id} shipment={shipmentOpen} canEdit={canDispatch} canCorrectHistory={rights.canUnship} close={() => setShipmentData(null)} onSaved={reload} />}
    {projectData && <DispatchCard project={projectData} customer={head[projectData]?.customer} canEdit={canDispatch} onSaved={() => void reload()} close={() => setProjectData(null)} />}
    {deliveryEdit && <ItemDeliveryWindow project={deliveryEdit.project} items={deliveryEdit.items} close={() => setDeliveryEdit(null)} onSaved={reload} />}
  </section>;
}
