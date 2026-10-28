import { Fragment, createContext, useContext, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { LoadMore } from "./LoadMore";
import { urlParam } from "./navigation";
import { SuggestInput } from "./SuggestInput";
import { LotLabel, labelsApi } from "./LabelPrint";
import { PrintSetupButton } from "./PrintSetupWindow";
import { UpdRecognitionWindow } from "./UpdRecognitionWindow";
import { usePurposeLabel } from "./purposeLabels";
import { VIA_LABELS, purchasingItemsApi, type ItemBrief, type ItemRow } from "./purchasingItemsApi";
import { useLatestSearch } from "./useLatestSearch";
import {
  receivingApi,
  requestsApi,
  warehouseApi,
  type WarehouseDestination,
  type WarehouseJournal,
  type WarehouseLot,
  type WarehouseLotPassport,
  type WarehouseRackLayout,
  type WarehouseRowEdit,
  type WarehouseTotals,
} from "./api";
import { createPortal } from "react-dom";
import { openPrintSheet } from "./print";
import { useCanPrint } from "./rights";
import { countLabel } from "./format";
import { BUSY_TITLE } from "./WarehousePlaceHint";
import { UndoLast, WarehouseScanResults, type ScanRequest } from "./WarehouseScan";
import { WarehouseReceipt } from "./WarehouseReceipt";
import { lotDocuments, receiptLine, shortDate } from "./warehouseReceiptLogic";
import { FinishedGoodsPanel } from "./FinishedGoodsPanel";
import { StockLinkDialog } from "./StockLinkDialog";
import { downloadStockTable, ItemTypesPanel, RackLayoutPanel, StockImportPanel, StockJournalPanel } from "./WarehouseReferences";
import {
  agreeing, chooserColumns, ColumnChooser, columnTitle, COLUMN_HINTS, defaultLayout, documentsLayout, moveColumn, readLayout, readLayouts,
  readLocalLayouts, shownColumns as visibleColumns, STOCK_VIEWS, TABLE_KINDS, writeLayouts,
  type ColumnKey, type ColumnLayout, type TableKind,
} from "./WarehouseColumns";
import { loadPersonalViews, migrateLocalView, resetPersonalView, savePersonalView } from "./personalViews";

/** Номенклатура на партиях (a failing read = no chip, no action). */
const LotItems = createContext<{ items: Record<string, ItemBrief>; reload: () => void }>({ items: {}, reload: () => undefined });

/** Lot passport as one line for the operations row (SK-6); empty fields are left out. */
function passportLine(lot: WarehouseLot) {
  return passportParts(lot.unit_mass_g ? `Масса 1 шт: ${lot.unit_mass_g} г` : "", lot);
}

function passportParts(mass: string, fields: Partial<Omit<WarehouseLotPassport, "mass_1" | "mass_10" | "mass_100">>) {
  return [
    mass,
    fields.expiry_date ? `Годен до ${fields.expiry_date.split("-").reverse().join(".")}` : "",
    fields.manufacturer ? `Производитель: ${fields.manufacturer}` : "",
    fields.description ? `Описание: ${fields.description}` : "",
    fields.storage_conditions ? `Условия хранения: ${fields.storage_conditions}` : "",
    fields.datasheet ? `Datasheet: ${fields.datasheet}` : "",
    fields.certificate ? `Сертификат: ${fields.certificate}` : "",
  ].filter(Boolean).join(" · ");
}

/** `admin` — the administrator sees «Журнал склада» (SK-16, SUPPLYMAP portalStock05Log is admin only).
 *  `readOnly` — «режим просмотра» (ID-6): the stock is read, every entry/move/writeoff/edit control is gone
 *  (SUPPLYMAP portalApplyAccess_ 'view' drops b.edit, portalStock05Guard_ refuses writes).
 *  `finishedGoods` — the viewer reads «Отгрузка»: the «Готовая продукция» tab (read-only, owner 12.10.2026). */
export function WarehousePage({ admin = false, readOnly = false, finishedGoods = false, canEditReferences = false }: { admin?: boolean; readOnly?: boolean; finishedGoods?: boolean; canEditReferences?: boolean } = {}) {
  // `?section=stock&view=goods` opens «Готовая продукция» (a link, a screenshot).
  const [view, setView] = useState<"stock" | "goods">(() => new URLSearchParams(window.location.search).get("view") === "goods" ? "goods" : "stock");
  if (finishedGoods && view === "goods") return <section className="page stock-page">
    <header className="page-head"><h1>Склад</h1></header>
    <StockViews view={view} onChange={setView} />
    <FinishedGoodsPanel />
  </section>;
  return <StockPage admin={admin} readOnly={readOnly} canEditReferences={canEditReferences} views={finishedGoods ? <StockViews view={view} onChange={setView} /> : null} />;
}

/** «Материалы» | «Готовая продукция» под заголовком склада. */
function StockViews({ view, onChange }: { view: "stock" | "goods"; onChange: (view: "stock" | "goods") => void }) {
  return <div className="seg-tabs stock-views" role="tablist" aria-label="Склад">
    {([["stock", "Материалы и комплектующие"], ["goods", "Готовая продукция"]] as const).map(([key, label]) =>
      <button key={key} type="button" role="tab" aria-selected={view === key} className={view === key ? "on" : undefined} onClick={() => onChange(key)}>{label}</button>)}
  </div>;
}

function StockPage({ admin, readOnly, canEditReferences, views }: { admin: boolean; readOnly: boolean; canEditReferences: boolean; views: ReactNode }) {
  const purposeText = usePurposeLabel();
  const [lots, setLots] = useState<WarehouseLot[]>([]);
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [totalsStamp, setTotalsStamp] = useState(0);
  const [error, setError] = useState("");
  const [query, setQuery] = useState(() => urlParam("q"));
  const searchRef = useRef<HTMLInputElement>(null);
  const [scanRequest, setScanRequest] = useState<ScanRequest | null>(null);
  const [lotItems, setLotItems] = useState<Record<string, ItemBrief>>({});
  const [itemsTick, setItemsTick] = useState(0);
  const [warehouse, setWarehouse] = useState("");
  const [includeZero, setIncludeZero] = useState(false);
  const [total, setTotal] = useState(0);
  const [adding, setAdding] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [moveLot, setMoveLot] = useState<WarehouseLot | null>(null);
  const [groupBy, setGroupBy] = useState<"" | "type" | "place" | "name" | "series" | "grn">("series");
  const [flat, setFlat] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "article", desc: false });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [panel, setPanel] = useState<"" | "types" | "import" | "journal" | "racks">("");
  const [updSetup, setUpdSetup] = useState(false);
  // Type suggestions (SK-14): the dictionary + the canonical types already on the stock.
  const [typeChoices, setTypeChoices] = useState<string[]>([]);
  // WH-6: печатный «Лист перемещения на склад» для принятых партий без места — тот же механизм,
  // что и FF-8 (frontend/src/print.ts): backend строит PrintSheet, окно печати открывает print.ts.
  const [putawayBusy, setPutawayBusy] = useState(false);
  // Своё сообщение: загрузка склада (через 250 мс после открытия) сбрасывает общую ошибку и стёрла бы его.
  const [putawayError, setPutawayError] = useState("");
  const requestVersion = useRef(0);
  // Lots «Внести позиции» has just put on the stock: a reload may not return them on its first page (the flat table is
  // in id order), so they stay in the table until the window is closed and the list is read once more.
  const receivedLots = useRef<WarehouseLot[]>([]);
  // Group and article totals come from the server for the whole filter, so they do not depend on how many lots are
  // loaded. The request follows the grouping and every (re)load/change (the stamp); filter changes bump the stamp
  // through reload(), which already waits for the typing to stop.
  const totalsState = useLatestSearch<WarehouseTotals | null>(
    () => flat ? Promise.resolve(null) : warehouseApi.totals({ group: groupBy, query, includeZero, warehouse }),
    [flat, groupBy, totalsStamp],
    { delay: 0, fallback: "Итоги не получены" },
  );
  const totals = totalsState.data?.group === groupBy ? totalsState.data : null;
  // «В резерве N шт · свободно M шт» for the whole filter: the server sums of the groups (grouped table) or the lots when all are loaded.
  const reserveTotal = useMemo(() => {
    const rows = flat ? (cursor ? null : [{ reserved: quantity(lots, "reserved"), available: quantity(lots, "available"), units: [...new Set(lots.map((lot) => lot.unit).filter(Boolean))] }]) : totals?.groups ?? null;
    if (!rows) return "";
    const units = [...new Set(rows.flatMap((row) => Number(row.reserved) > 0 ? row.units : []))];
    const round = (value: number) => Math.round(value * 1_000_000) / 1_000_000;
    const sum = round(rows.reduce((acc, row) => acc + Number(row.reserved), 0));
    const free = round(rows.reduce((acc, row) => acc + Number(row.available), 0));
    return sum > 0 && units.length === 1 ? `в резерве ${sum} ${units[0]} · свободно ${free} ${units[0]}` : "";
  }, [flat, cursor, lots, totals]);
  const groupedLots = useMemo(() => warehouseGroups(lots, groupBy, sort, totals, !cursor), [lots, groupBy, sort, totals, cursor]);
  // «Колонки»: each table has its own order and set of columns, the viewer changes both. The layout is the person's own,
  // kept on the server (it follows them to another PC); this browser's copy opens the screen at once and stands in
  // while the server is out of reach. The name is always there; the column the rows are grouped by is the group header
  // already, not repeated in every row.
  const [layouts, setLayouts] = useState(readLayouts);
  // What this browser kept before this page wrote its own copy (a table never changed here is absent).
  const [localAtStart] = useState(readLocalLayouts);
  // Tables the viewer changed before the server answered keep that change.
  const touchedKinds = useRef(new Set<TableKind>());
  useEffect(() => {
    let live = true;
    void loadPersonalViews().then((views) => {
      if (!live || !views) return;
      const local = localAtStart;
      const fromServer: Partial<Record<TableKind, ColumnLayout>> = {};
      for (const tableKind of TABLE_KINDS) {
        if (touchedKinds.current.has(tableKind)) continue;
        const saved = views[STOCK_VIEWS[tableKind]];
        if (saved && typeof saved === "object") fromServer[tableKind] = readLayout(tableKind, saved);
        else migrateLocalView(STOCK_VIEWS[tableKind], ownLayout(tableKind, local[tableKind]));
      }
      if (Object.keys(fromServer).length) setLayouts((current) => ({ ...current, ...fromServer }));
    });
    return () => { live = false; };
  }, []);
  const kind: TableKind = flat ? "flat" : "grouped";
  const layout = layouts[kind];
  const groupColumn = !flat && (groupBy === "series" || groupBy === "type") ? groupBy : null;
  const shownColumns = visibleColumns(layout, groupColumn);
  // Document columns switched on widen the table (it scrolls in its frame) instead of squeezing the name.
  const docsLayout = documentsLayout(shownColumns);
  const chooserKeys = chooserColumns(layout, groupColumn);
  // The leftmost column stays in view when the table is scrolled sideways.
  const pinned = shownColumns[0];
  const changeLayout = (change: (current: ColumnLayout) => ColumnLayout) => {
    const next = change(layouts[kind]);
    touchedKinds.current.add(kind);
    setLayouts((current) => ({ ...current, [kind]: next }));
    savePersonalView(STOCK_VIEWS[kind], next);
  };
  const resetLayout = () => {
    touchedKinds.current.add(kind);
    setLayouts((current) => ({ ...current, [kind]: defaultLayout(kind) }));
    resetPersonalView(STOCK_VIEWS[kind]);
  };
  useEffect(() => writeLayouts(layouts), [layouts]);
  // The opened lot cards stay inside the visible part of a table wider than the screen: pinned to the left edge and
  // as wide as the scroll box, not the table (the same as DataTable's detail row).
  const wrapRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap || typeof ResizeObserver === "undefined") return;
    const sync = () => wrap.style.setProperty("--detail-width", `${wrap.clientWidth}px`);
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, [flat]);

  /** The grouped table is ordered by the server over the whole filter (a page holds the top of the whole warehouse,
   *  not of what happened to be loaded); the flat table keeps the id order. */
  const serverOrder = () => flat ? {} : { sort: sort.key, desc: sort.desc, group: groupBy };

  /** The first server page only: the table is on screen after one request; the rest comes on demand. */
  async function reload(received: WarehouseLot[] = receivedLots.current) {
    const version = ++requestVersion.current;
    setTotalsStamp((value) => value + 1);
    setLoading(true);
    setError("");
    try {
      const page = await warehouseApi.list({ query, includeZero, warehouse, ...serverOrder() });
      if (version !== requestVersion.current) return;
      setLots(mergeLots([], [...page.lots, ...received]));
      setTotal(page.total);
      setCursor(page.next_cursor);
    } catch (reason) {
      if (version !== requestVersion.current) return;
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить склад");
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }

  /** «Показать ещё» — the next server page; «Загрузить всё» — every remaining page (group totals need them). */
  async function loadMore(all: boolean) {
    if (!cursor || loadingMore) return;
    const version = requestVersion.current;
    setLoadingMore(true);
    setError("");
    try {
      let next: string | null = cursor;
      let loaded = lots;
      do {
        const page = await warehouseApi.list({ after: next, query, includeZero, warehouse, ...serverOrder() });
        if (version !== requestVersion.current) return;
        loaded = mergeLots(loaded, page.lots);
        setLots(loaded);
        setTotal(page.total);
        next = page.next_cursor;
        setCursor(next);
      } while (all && next);
    } catch (reason) {
      if (version === requestVersion.current) setError(reason instanceof Error ? reason.message : "Не удалось загрузить склад");
    } finally {
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    // Suggestions only: a failed load leaves the type field free, as before. The dictionary is an edit tool.
    if (readOnly) return;
    warehouseApi.itemTypes().then((types) => setTypeChoices(Array.isArray(types?.choices) ? types.choices : []), () => undefined);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void reload(), 250);
    return () => window.clearTimeout(timer);
  }, [query, warehouse, includeZero, flat, sort.key, sort.desc, groupBy]);

  useEffect(() => {
    if (lots.length === 0) { setLotItems({}); return; }
    let live = true;
    const ids = lots.map((lot) => lot.id);
    const parts: string[][] = [];
    for (let start = 0; start < ids.length; start += 500) parts.push(ids.slice(start, start + 500));
    // One batch per 500 visible lots; a failed read just leaves the lots without a chip.
    Promise.all(parts.map((part) => purchasingItemsApi.forLots(part)))
      .then((answers) => { if (live) setLotItems(Object.assign({}, ...answers)); })
      .catch(() => { if (live) setLotItems({}); });
    return () => { live = false; };
  }, [lots, itemsTick]);

  /** Closing «Внести позиции» reads the stock once more, so the table behind shows what was accepted. */
  function closeReceipt() {
    const received = receivedLots.current;
    receivedLots.current = [];
    setAdding(false);
    if (received.length > 0) void reload([]);
  }

  const canPrint = useCanPrint();
  async function openPutawaySheet() {
    setPutawayError("");
    setPutawayBusy(true);
    try {
      const { sheet } = await receivingApi.putawaySheet();
      openPrintSheet(sheet);
    } catch (reason) {
      setPutawayError(reason instanceof Error ? reason.message : "Не удалось сформировать лист перемещения");
    } finally {
      setPutawayBusy(false);
    }
  }

  async function mutate(key: string, operation: () => Promise<unknown>) {
    if (busy || loading) return;
    requestVersion.current += 1;
    setBusy(key);
    setError("");
    try {
      const result = await operation();
      // The server answers with the touched lot(s): only those rows change, nothing is re-read.
      const touched = touchedLots(result);
      if (touched) { setLots((current) => replaceLots(current, touched)); setTotalsStamp((value) => value + 1); }
      else await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Складская операция не выполнена");
      throw reason;
    } finally {
      setBusy("");
    }
  }

  return (
    <LotItems.Provider value={{ items: lotItems, reload: () => setItemsTick((n) => n + 1) }}>
    <section className="page stock-page">
      <header className="page-head">
        <h1>Склад</h1>
        <span className="page-count">{total}</span>
        {reserveTotal && <span className="page-sub" role="status">{reserveTotal}</span>}
        {/* The daily actions stay in sight; the reference tools sit behind «Ещё» (every one still one click away). */}
        <div className="page-actions">
          <button className="secondary" disabled={loading || Boolean(busy)} onClick={() => void reload()}>{loading ? "Обновляем…" : "Обновить"}</button>
          {!readOnly && canPrint && <button className="secondary" disabled={putawayBusy} onClick={() => void openPutawaySheet()}>Лист перемещения</button>}
          {!readOnly && <button className="secondary" disabled={loading || Boolean(busy)} onClick={() => { setMoveLot(null); setMoveOpen(true); }}>↔ Переместить</button>}
          <MoreMenu items={[
            ...(readOnly ? [] : [
              { label: "Стеллажи", run: () => setPanel((value) => value === "racks" ? "" : "racks") },
              { label: "Типы", run: () => setPanel((value) => value === "types" ? "" : "types") },
              { label: "Выгрузить", disabled: Boolean(busy), run: () => {
                setError("");
                downloadStockTable().catch((reason) => setError(reason instanceof Error ? reason.message : "Не удалось выгрузить."));
              } },
              { label: "Загрузить правки", run: () => setPanel((value) => value === "import" ? "" : "import") },
            ]),
            ...(admin ? [
              { label: "Журнал склада", run: () => setPanel((value) => value === "journal" ? "" : "journal") },
              { label: "Распознавание УПД", run: () => setUpdSetup(true) },
            ] : []),
          ]} />
          {updSetup && <UpdRecognitionWindow close={() => setUpdSetup(false)} />}
          <PrintSetupButton kind="lot" readOnly={readOnly} />
          {!readOnly && <button className="primary" disabled={loading || Boolean(busy)} onClick={() => { if (adding) closeReceipt(); else setAdding(true); }}>+ Внести позиции</button>}
        </div>
      </header>
      {views}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {putawayError && <div className="error-banner" role="alert">{putawayError}</div>}
      {/* Wording from SUPPLYMAP «Склад 0.5» (PortalApp.html wh-hint). */}
      {readOnly && <p className="page-sub">Склад открыт вам только на просмотр.</p>}
      <div className="filters table-toolbar stock-toolbar">
        {/* One field for both (owner 22.10): typing filters the table; with warehouse.scan Enter opens the ГРН / place. */}
        <form className="stock-search-form" role="search"
          onSubmit={(event) => { event.preventDefault(); setScanRequest({ code: query, at: Date.now() }); }}>
          <input ref={searchRef} className="search stock-search" aria-label="Поиск по складу" autoComplete="off" spellCheck={false}
            placeholder="Скан, ГРН, место или слова: артикул, название…"
            title="Enter по ГРН или месту открывает карточку"
            value={query} onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Escape") { setQuery(""); setScanRequest(null); } }} />
        </form>
        <label className="check-inline"><input type="checkbox" checked={includeZero} onChange={(event) => setIncludeZero(event.target.checked)} />показывать нулевые</label>
        <label className="inline-select">группировать<select aria-label="Группировка склада" value={groupBy} onChange={(event) => setGroupBy(event.target.value as typeof groupBy)}>
          <option value="">нет</option><option value="type">по типу</option><option value="place">по месту (полка)</option><option value="name">по наименованию</option><option value="series">по серии</option><option value="grn">по ГРН</option>
        </select></label>
        <select aria-label="Склад" value={warehouse} onChange={(event) => setWarehouse(event.target.value)}>
          <option value="">Все склады</option><option value="ЦАР">ЦАР</option><option value="ЖУК">ЖУК</option>
        </select>
        <div className="toolbar-end">
          <button className="secondary" aria-pressed={flat} onClick={() => setFlat((value) => !value)}>{flat ? "Остатки по позициям" : "Таблица склада"}</button>
          <ColumnChooser
            keys={chooserKeys}
            hidden={layout.hidden}
            flat={flat}
            onToggle={(key, shown) => changeLayout((current) => ({ ...current, hidden: shown ? current.hidden.filter((item) => item !== key) : [...current.hidden, key] }))}
            onMove={(key, step) => changeLayout((current) => ({ ...current, order: moveColumn(current.order, key, step, chooserKeys) }))}
            onReset={resetLayout}
          />
        </div>
      </div>
      <WarehouseScanResults request={scanRequest} inputRef={searchRef} readOnly={readOnly}
        disabled={Boolean(busy) || loading} mutate={mutate} onClose={() => setScanRequest(null)} />
      {panel === "types" && <ItemTypesPanel
        onClose={() => setPanel("")}
        onChanged={async (types, renamed) => {
          setTypeChoices(types.choices);
          if (renamed) await reload();
        }}
      />}
      {panel === "import" && <StockImportPanel onClose={() => setPanel("")} onApplied={() => reload()} />}
      {panel === "racks" && <RackLayoutPanel onClose={() => setPanel("")} />}
      {panel === "journal" && <StockJournalPanel onClose={() => setPanel("")} />}
      {adding && <WarehouseReceipt
        knownLots={lots}
        typeChoices={typeChoices}
        onClose={() => closeReceipt()}
        onAccepted={(accepted) => {
          receivedLots.current = mergeLots(receivedLots.current, [accepted]);
          requestVersion.current += 1;
          setLots((current) => mergeLots(current, [accepted]));
          void reload();
        }}
      />}
      {moveOpen && <MoveStockDialog
        preset={moveLot}
        lots={lots}
        busy={Boolean(busy) || loading}
        mutate={mutate}
        onClose={() => setMoveOpen(false)}
      />}
      {flat ? <div className="table-wrap" ref={wrapRef}>
        <table className="grid wide compact stock-table" aria-label="Таблица склада"
          style={docsLayout ? { minWidth: docsLayout.minWidth } : undefined}>
          <colgroup>{shownColumns.map((key) => <col key={key} className={`stock-col-${key}`}
            style={docsLayout?.widths[key] ? { width: docsLayout.widths[key] } : undefined} />)}</colgroup>
          <thead><tr>{shownColumns.map((key) => <th key={key} className={classes(columnClass(key), key === pinned && "stock-pin")} title={COLUMN_HINTS[key]}>{columnTitle(key, true)}</th>)}</tr></thead>
          <tbody>{lots.map((lot) => <tr key={lot.id}>{shownColumns.map((key) => flatCell(key, lot, key === pinned, lotItems[lot.id], purposeText))}</tr>)}</tbody>
        </table>
      </div> : <div className="table-wrap" ref={wrapRef}>
        <table className="grid wide compact stock-table" style={docsLayout ? { minWidth: docsLayout.minWidth } : undefined}>
          <colgroup>{shownColumns.map((key) => <col key={key} className={`stock-col-${key}`}
            style={docsLayout?.widths[key] ? { width: docsLayout.widths[key] } : undefined} />)}</colgroup>
          {/* The server orders the whole filter by every sortable column (`SORT_KEYS`), lot by lot, before cutting the page. */}
          <thead><tr>{shownColumns.map((key) => <th key={key} className={classes(columnClass(key), key === pinned && "stock-pin")} aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : undefined}>
            {isSortKey(key)
              ? <button type="button" className="th-sort" title={COLUMN_HINTS[key]} onClick={() => setSort((current) => ({ key, desc: current.key === key ? !current.desc : false }))}>{columnTitle(key, false)}{sort.key === key ? (sort.desc ? " ▼" : " ▲") : ""}</button>
              : <span title={COLUMN_HINTS[key]}>{columnTitle(key, false)}</span>}
          </th>)}</tr></thead>
          <tbody>{groupedLots.map((group) => <ArticleGroup
            key={group.key}
            group={group}
            columns={shownColumns}
            pinned={pinned}
            expanded={expanded}
            toggle={(key) => setExpanded((current) => {
              const next = new Set(current);
              if (next.has(key)) next.delete(key); else next.add(key);
              return next;
            })}
            busy={busy}
            disabled={Boolean(busy) || loading}
            mutate={mutate}
            onMove={(item) => { setMoveLot(item); setMoveOpen(true); }}
            history={admin || !readOnly}
            readOnly={readOnly}
            canLink={canEditReferences}
          />)}</tbody>
        </table>
      </div>}
      {cursor && <div className="table-more">
        <span className="page-sub" role="status">Показано {lots.length} из {total}.{totalsState.error ? ` ${totalsState.error}.` : ""}</span>
        <LoadMore busy={loadingMore || loading} token={lots.length} onMore={() => void loadMore(false)} />
        <button className="secondary small" disabled={loadingMore || loading} onClick={() => void loadMore(true)}>Загрузить всё</button>
      </div>}
      {!lots.length && !loading && <div className="empty">{includeZero ? "Склад пуст." : "Позиций с остатком нет. Включите «показывать нулевые», чтобы увидеть израсходованные партии."}</div>}
    </section>
    </LotItems.Provider>
  );
}

/** A layout this browser kept that differs from the table's default (every visit used to write the default too). */
function ownLayout(kind: TableKind, stored: unknown): ColumnLayout | null {
  if (!stored || typeof stored !== "object") return null;
  const layout = readLayout(kind, stored);
  const fallback = defaultLayout(kind);
  const same = layout.order.join() === fallback.order.join() && [...layout.hidden].sort().join() === [...fallback.hidden].sort().join();
  return same ? null : layout;
}

/** Lots with the same id once, in the order they came. */
function mergeLots(current: WarehouseLot[], more: WarehouseLot[]) {
  return [...new Map([...current, ...more].map((lot) => [lot.id, lot])).values()];
}

/** The lots an operation returned (`lot` or `lots`), or null when its answer names none. */
function touchedLots(result: unknown): WarehouseLot[] | null {
  const answer = result as { lot?: WarehouseLot; lots?: WarehouseLot[] } | null | undefined;
  if (answer?.lot?.id) return [answer.lot];
  if (Array.isArray(answer?.lots) && answer.lots.every((lot) => lot?.id)) return answer.lots;
  return null;
}

function replaceLots(current: WarehouseLot[], touched: WarehouseLot[]) {
  const byId = new Map(touched.map((lot) => [lot.id, lot]));
  return current.map((lot) => byId.has(lot.id) ? { ...lot, ...byId.get(lot.id)! } : lot);
}

/** Sums of an article or a group over every lot of the filter; null while the server has not answered and not all lots are loaded. */
type RowTotals = { balance: string; reserved?: string; available?: string; lots: number; units: string[] };

type WarehouseArticle = {
  key: string;
  article: string;
  lots: WarehouseLot[];
  totals: RowTotals | null;
};

type WarehouseGroup = {
  key: string;
  label: string;
  articles: WarehouseArticle[];
  totals: (RowTotals & { articles: number }) | null;
};

const warehouseCollator = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });

function articleKey(lot: WarehouseLot) {
  const identity = (lot.article || lot.name).trim().replace(/\s+/g, " ").toLocaleLowerCase("ru");
  return identity || `\u0001грн ${lot.grn}/${lot.package}`;
}

function shelf(location: string) {
  return location.replace(/\s+/g, "").toUpperCase().match(/^(\d+[A-ZА-Я])/)?.[1] ?? location;
}

/** The columns the grouped table is ordered by (the server's `sort`): every column but «Назначение» and «Файл УПД». */
const SORT_KEYS = [
  "type", "grn", "balance", "reserved", "available", "lots", "place", "series", "article", "name", "warehouse",
  "maker", "maker_article", "supplier_article", "upd_name", "invoice_name", "invoice_number", "invoice_date", "upd_number", "upd_date", "prd_number", "prd_date",
] as const;
type SortKey = typeof SORT_KEYS[number];
const isSortKey = (key: ColumnKey): key is SortKey => (SORT_KEYS as readonly string[]).includes(key);

function columnClass(key: ColumnKey) {
  return key === "name" ? "stock-name" : key === "balance" || key === "lots" || key === "available" || key === "reserved" ? "num" : undefined;
}

function classes(...names: Array<string | false | undefined>) {
  return names.filter(Boolean).join(" ") || undefined;
}

/** «Ещё ▾»: the reference tools of the page behind one button; every item is a plain button of the menu. */
function MoreMenu({ items }: { items: Array<{ label: string; disabled?: boolean; run: () => void }> }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    // A tap outside, or the focus leaving the menu, closes it (a blur handler would eat the click on Safari/iPad).
    const leave = (event: Event) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", leave);
    document.addEventListener("focusin", leave);
    return () => {
      document.removeEventListener("pointerdown", leave);
      document.removeEventListener("focusin", leave);
    };
  }, [open]);
  if (!items.length) return null;

  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && open) {
      event.stopPropagation();
      setOpen(false);
      root.current?.querySelector<HTMLButtonElement>(".more-menu-trigger")?.focus();
    } else if (open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      const buttons = [...(root.current?.querySelectorAll<HTMLButtonElement>("[role='menuitem']:not(:disabled)") ?? [])];
      const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
      const step = event.key === "ArrowDown" ? 1 : -1;
      buttons[(at + step + buttons.length) % buttons.length]?.focus();
    }
  }

  return <div className="more-menu" ref={root} onKeyDown={onKeyDown}>
    <button type="button" className="secondary more-menu-trigger" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>Ещё ▾</button>
    {open && <div className="more-menu-list" role="menu">
      {items.map((item) => <button key={item.label} type="button" role="menuitem" disabled={item.disabled} onClick={() => { setOpen(false); item.run(); }}>{item.label}</button>)}
    </div>}
  </div>;
}

/** A row is one lot (27.10), so the value is that lot's own, as the server's `sort_stock_lots` reads it. */
function sortValue(item: WarehouseArticle, key: SortKey): string | number {
  const lot = item.lots[0];
  switch (key) {
    case "type": return distinct(item.lots, "item_type");
    case "grn": return distinct(item.lots, "grn");
    case "balance": return Number(item.totals?.balance ?? quantity(item.lots, "balance"));
    case "reserved": return Number(item.totals?.reserved ?? quantity(item.lots, "reserved"));
    case "available": return Number(item.totals?.available ?? quantity(item.lots, "available"));
    case "lots": return item.totals?.lots ?? item.lots.length;
    case "place": return distinct(item.lots, "storage_location");
    case "series": return distinct(item.lots, "series");
    case "name": return distinct(item.lots, "name");
    case "warehouse": return distinct(item.lots, "warehouse");
    case "maker": return lot?.manufacturer ?? "";
    case "maker_article": return lot?.maker_article ?? "";
    case "supplier_article": return lot?.supplier_article ?? "";
    case "upd_name": return lot?.upd_name ?? "";
    case "invoice_name": return lot?.invoice_name ?? "";
    case "invoice_number": case "invoice_date": case "upd_number": case "upd_date": case "prd_number": case "prd_date":
      return lot?.receipt?.[key] ?? "";
    default: return item.article;
  }
}

function compareArticles(left: WarehouseArticle, right: WarehouseArticle, sort: { key: SortKey; desc: boolean }) {
  const a = sortValue(left, sort.key);
  const b = sortValue(right, sort.key);
  const order = typeof a === "number" && typeof b === "number" ? a - b : warehouseCollator.compare(String(a), String(b));
  if (order) return sort.desc ? -order : order;
  // Ties as on the server: the article key, then the lot id — the loaded page continues the server's order.
  const [leftLot, rightLot] = [left.lots[0], right.lots[0]];
  const byArticle = leftLot && rightLot ? warehouseCollator.compare(articleKey(leftLot), articleKey(rightLot)) : 0;
  return byArticle || (leftLot && rightLot ? (leftLot.id < rightLot.id ? -1 : leftLot.id > rightLot.id ? 1 : 0) : warehouseCollator.compare(left.key, right.key));
}

function loadedTotals(lots: WarehouseLot[]): RowTotals {
  return { balance: quantity(lots, "balance"), reserved: quantity(lots, "reserved"), available: quantity(lots, "available"), lots: lots.length, units: [...new Set(lots.map((lot) => lot.unit).filter(Boolean))] };
}

function warehouseGroups(
  lots: WarehouseLot[],
  groupBy: "" | "type" | "place" | "name" | "series" | "grn",
  sort: { key: SortKey; desc: boolean },
  totals: WarehouseTotals | null = null,
  complete = false,
): WarehouseGroup[] {
  const byGroup = new Map<string, WarehouseTotals["groups"][number]>((totals?.groups ?? []).map((item) => [item.group, item]));
  const groupMap = new Map<string, WarehouseLot[]>();
  for (const lot of lots) {
    const value = groupBy === "type" ? lot.item_type
      : groupBy === "place" ? shelf(lot.storage_location)
      : groupBy === "name" ? lot.name
      : groupBy === "series" ? lot.series
      : groupBy === "grn" ? lot.grn
      : "";
    const key = value.trim();
    groupMap.set(key, [...(groupMap.get(key) ?? []), lot]);
  }
  return [...groupMap.entries()]
    .sort(([left], [right]) => !left ? 1 : !right ? -1 : warehouseCollator.compare(left, right))
    .map(([groupKey, groupLots]) => {
      const articleMap = new Map<string, WarehouseLot[]>();
      for (const lot of groupLots) {
        const key = articleKey(lot);
        articleMap.set(key, [...(articleMap.get(key) ?? []), lot]);
      }
      const groupTotal = byGroup.get(groupKey);
      return {
        totals: groupTotal ? { ...groupTotal } : complete ? { ...loadedTotals(groupLots), articles: articleMap.size } : null,
        key: `${groupBy}:${groupKey}`,
        label: !groupBy ? "" : groupKey || ({ type: "Без типа", place: "Без места", name: "Без наименования", series: "Без серии", grn: "Без ГРН" })[groupBy],
        // A row is one lot (owner 27.10.2026: lots of one article differ in ГРН, place and documents — «объединять
        // одинаковый артикул неправильно»); the group heading still counts the articles.
        articles: groupLots
          .map((lot) => ({
            key: `${groupBy}:${groupKey}:${articleKey(lot)}:${lot.id}`,
            article: lot.article || lot.name,
            lots: [lot],
            totals: loadedTotals([lot]),
          }))
          .sort((left, right) => compareArticles(left, right, sort)),
      };
    });
}

function distinct(lots: WarehouseLot[], field: keyof WarehouseLot) {
  return [...new Set(lots.map((lot) => String(lot[field] ?? "")).filter(Boolean))].join(" · ");
}

/** Places as in SUPPLYMAP: the first three, then «+N». */
function places(lots: WarehouseLot[]) {
  const all = [...new Set(lots.map((lot) => lot.storage_location).filter(Boolean))];
  return all.slice(0, 3).join(" · ") + (all.length > 3 ? ` +${all.length - 3}` : "");
}

function quantity(lots: WarehouseLot[], field: "balance" | "available" | "reserved") {
  return String(Math.round(lots.reduce((sum, lot) => sum + Number(lot[field]), 0) * 1_000_000) / 1_000_000);
}

/** «Под какие заявки»: one line per holder of the reserve — «REQ.26.0000012 · 719К.2 КБ12 — 100 шт». */
function holdersTitle(lots: WarehouseLot[]) {
  return lots.flatMap((lot) => lot.reserve_holders ?? [])
    .map((holder) => `${holder.label} — ${holder.quantity} ${holder.unit}`.trim()).join("\n") || undefined;
}

/** Group header: free stock first when something is reserved — «свободно 21 шт (остаток 101, резерв 80)»; otherwise «остаток 101 шт». */
function groupStockText(totals: RowTotals, unit: string) {
  if (!(Number(totals.reserved) > 0)) return ` · остаток ${totals.balance} ${unit}`;
  const free = totals.available ?? String(Math.round((Number(totals.balance) - Number(totals.reserved)) * 1_000_000) / 1_000_000);
  return ` · свободно ${free} ${unit} (остаток ${totals.balance}, резерв ${totals.reserved})`;
}

/** Tooltip of «Свободно»: the raw balance and the reserve it is made of, «остаток 101 шт · резерв 80 шт». */
function balanceTitle(balance: string, reserved: string, unit: string) {
  const tail = unit ? ` ${unit}` : "";
  return `остаток ${balance}${tail}${Number(reserved) > 0 ? ` · резерв ${reserved}${tail}` : ""}`;
}

/** «Резерв»: the quantity with the unit, a dim «—» when nothing is reserved; the tooltip says by whom. */
function reserveCell(key: ColumnKey, amount: string, unit: string, lots: WarehouseLot[], pinned: boolean) {
  const held = Number(amount) > 0;
  return <td key={key} className={classes("num", pinned && "stock-pin", !held && "stock-dim")} title={held ? holdersTitle(lots) : undefined}>{held ? `${amount}${unit ? ` ${unit}` : ""}` : "—"}</td>;
}

/** A cell cut to its column with «…»; the full text is the tooltip. */
function cutCell(key: ColumnKey, text: string, pinned: boolean, className = "") {
  return <td key={key} className={classes("cut", className, pinned && "stock-pin")} title={text || undefined}>{text}</td>;
}

/** One lot of «Таблица склада», cell by cell in the order of the visible columns. */
function flatCell(key: ColumnKey, lot: WarehouseLot, pinned: boolean, brief?: ItemBrief, purposeText: (value: string) => string = (value) => value) {
  switch (key) {
    case "name": return <td key={key} className={classes("stock-name", pinned && "stock-pin")} title={lot.name || undefined}>{lot.name || "—"}{brief && itemMark(lot.name || "", brief.name, `Позиция номенклатуры (связь: ${VIA_LABELS[brief.via]})`)}</td>;
    case "article": return cutCell(key, lot.article || "—", pinned);
    case "grn": return cutCell(key, `${lot.grn}${lot.package ? ` · ${lot.package}` : ""}`, pinned, "stock-grn");
    case "balance": return <td key={key} className={classes("num", pinned && "stock-pin")}><strong>{lot.balance} {lot.unit}</strong></td>;
    case "available": return <td key={key} className={classes("num", pinned && "stock-pin")} title={balanceTitle(lot.balance, lot.reserved, lot.unit)}><strong>{lot.available} {lot.unit}</strong></td>;
    case "reserved": return reserveCell(key, lot.reserved, lot.unit, [lot], pinned);
    case "place": return cutCell(key, lot.storage_location || "—", pinned);
    case "warehouse": return cutCell(key, lot.warehouse, pinned);
    case "purpose": return cutCell(key, purposeText(lot.purpose) || "—", pinned);
    case "series": return cutCell(key, lot.series || "—", pinned);
    case "type": return typeCell(key, lot.item_type, pinned);
    case "maker": return dimCell(key, lot.manufacturer ?? "", pinned);
    case "maker_article": return cutCell(key, lot.maker_article || "", pinned);
    case "supplier_article": return cutCell(key, lot.supplier_article || "", pinned);
    case "upd_name": return cutCell(key, lot.upd_name || "", pinned);
    case "invoice_name": return cutCell(key, lot.invoice_name || "", pinned);
    case "invoice_number": return cutCell(key, lot.receipt?.invoice_number || "", pinned);
    case "invoice_date": return cutCell(key, shortDate(lot.receipt?.invoice_date), pinned, "num");
    case "upd_number": return cutCell(key, lot.receipt?.upd_number || "", pinned);
    case "upd_date": return cutCell(key, shortDate(lot.receipt?.upd_date), pinned, "num");
    case "prd_number": return cutCell(key, lot.receipt?.prd_number || "", pinned);
    case "prd_date": return cutCell(key, shortDate(lot.receipt?.prd_date), pinned, "num");
    case "upd_file": return <td key={key} className={classes(pinned && "stock-pin")}>
      {lot.receipt?.scan_file ? <a href={lot.receipt.scan_file} target="_blank" rel="noreferrer" aria-label={`Файл УПД партии ${lot.grn}`}>открыть</a> : null}
    </td>;
    default: return null;
  }
}

/** The nomenclature item next to a lot or article name: a full second line only when it is named differently,
 *  otherwise a quiet ◆ — the same words twice is noise. */
function itemMark(name: string, itemName: string | undefined, title: string) {
  if (!itemName) return null;
  const same = itemName.trim().toLocaleLowerCase("ru") === name.trim().toLocaleLowerCase("ru");
  return same
    ? <span className="lot-item-mark" title={`${title}: ${itemName}`} aria-label={`${title}: ${itemName}`}>◆</span>
    : <small className="muted lot-item-chip" title={title}>{itemName}</small>;
}

/** One article (all its lots) of the grouped table, cell by cell in the order of the visible columns. */
function articleCell(key: ColumnKey, item: WarehouseArticle, open: boolean, toggle: () => void, pinned: boolean, briefs: Record<string, ItemBrief> = {}) {
  switch (key) {
    case "name": {
      const name = distinct(item.lots, "name") || item.article || "Без наименования";
      // Nomenclature item(s) of the article's lots («Номенклатура»): one name, or «N позиций» when they differ.
      const items = [...new Set(item.lots.map((lot) => briefs[lot.id]?.name).filter(Boolean))];
      const chip = items.length === 1 ? items[0] : items.length > 1 ? `${items.length} позиции номенклатуры` : "";
      return <td key={key} className={classes("stock-name", pinned && "stock-pin")}><button className="link-button table-link" title={name} aria-expanded={open} aria-label={`${open ? "Скрыть" : "Показать"} партии (${item.lots.length})`} onClick={(event) => { event.stopPropagation(); toggle(); }}>{name}</button>{chip && itemMark(name, chip, "Позиция номенклатуры")}</td>;
    }
    case "article": return cutCell(key, item.article || "—", pinned);
    case "grn": return grnCell(key, item.lots, pinned);
    case "balance": {
      if (!item.totals) return <td key={key} className={classes("num", pinned && "stock-pin")} title="Итог считается">…</td>;
      const unit = item.totals.units.join(" · ");
      return <td key={key} className={classes("num", pinned && "stock-pin")}><strong>{item.totals.balance}{unit ? ` ${unit}` : ""}</strong></td>;
    }
    case "reserved": {
      if (!item.totals) return <td key={key} className={classes("num", pinned && "stock-pin")} title="Итог считается">…</td>;
      return reserveCell(key, item.totals.reserved ?? quantity(item.lots, "reserved"), item.totals.units.join(" · "), item.lots, pinned);
    }
    case "available": {
      if (!item.totals) return <td key={key} className={classes("num", pinned && "stock-pin")} title="Итог считается">…</td>;
      const unit = item.totals.units.join(" · ");
      const free = item.totals.available ?? quantity(item.lots, "available");
      return <td key={key} className={classes("num", pinned && "stock-pin")} title={balanceTitle(item.totals.balance, item.totals.reserved ?? quantity(item.lots, "reserved"), unit)}><strong>{free}{unit ? ` ${unit}` : ""}</strong></td>;
    }
    case "lots": return <td key={key} className={classes("num", pinned && "stock-pin")}>{item.totals ? item.totals.lots : "…"}</td>;
    case "place": return <td key={key} className={classes("cut", pinned && "stock-pin")} title={distinct(item.lots, "storage_location") || undefined}>{places(item.lots)}</td>;
    case "warehouse": return dimCell(key, distinct(item.lots, "warehouse"), pinned);
    case "series": return dimCell(key, distinct(item.lots, "series"), pinned);
    case "type": return typeCell(key, distinct(item.lots, "item_type"), pinned);
    case "maker": return agreeCell(key, item.lots.map((lot) => lot.manufacturer ?? ""), pinned);
    case "maker_article": return agreeCell(key, item.lots.map((lot) => lot.maker_article ?? ""), pinned);
    case "supplier_article": return agreeCell(key, item.lots.map((lot) => lot.supplier_article ?? ""), pinned);
    case "upd_name": return agreeCell(key, item.lots.map((lot) => lot.upd_name ?? ""), pinned);
    case "invoice_name": return agreeCell(key, item.lots.map((lot) => lot.invoice_name ?? ""), pinned);
    case "invoice_number": return agreeCell(key, item.lots.map((lot) => lot.receipt?.invoice_number ?? ""), pinned);
    case "invoice_date": return agreeCell(key, item.lots.map((lot) => shortDate(lot.receipt?.invoice_date)), pinned, "num");
    case "upd_number": return agreeCell(key, item.lots.map((lot) => lot.receipt?.upd_number ?? ""), pinned);
    case "upd_date": return agreeCell(key, item.lots.map((lot) => shortDate(lot.receipt?.upd_date)), pinned, "num");
    case "prd_number": return agreeCell(key, item.lots.map((lot) => lot.receipt?.prd_number ?? ""), pinned);
    case "prd_date": return agreeCell(key, item.lots.map((lot) => shortDate(lot.receipt?.prd_date)), pinned, "num");
    case "upd_file": {
      // One file — «открыть»; the lots of several deliveries — a numbered link per file.
      const files = [...new Map(item.lots.filter((lot) => lot.receipt?.scan_file).map((lot) => [lot.receipt!.scan_file!, lot.grn])).entries()];
      return <td key={key} className={classes(pinned && "stock-pin", !files.length && "stock-dim")} onClick={(event) => event.stopPropagation()}>
        {!files.length ? "—" : files.length === 1
          ? <a href={files[0][0]} target="_blank" rel="noreferrer" aria-label={`Файл УПД партии ${files[0][1]}`}>открыть</a>
          : files.map(([file, grn], index) => <Fragment key={file}>{index > 0 && " "}<a href={file} target="_blank" rel="noreferrer" title={`Файл УПД партии ${grn}`} aria-label={`Файл УПД партии ${grn}`}>{index + 1}</a></Fragment>)}
      </td>;
    }
    default: return null;
  }
}

/** A text cell with a dim «—» when empty. */
function dimCell(key: ColumnKey, text: string, pinned: boolean) {
  return text ? cutCell(key, text, pinned) : <td key={key} className={classes("cut stock-dim", pinned && "stock-pin")}>—</td>;
}

/** «Тип» wraps to two lines («Коаксиальный / соединитель») instead of being cut to «Коаксиальный с…». */
function typeCell(key: ColumnKey, text: string, pinned: boolean) {
  if (!text) return <td key={key} className={classes("stock-dim", pinned && "stock-pin")}>—</td>;
  return <td key={key} className={classes("stock-wrap", pinned && "stock-pin")} title={text}><span className="stock-clamp">{text}</span></td>;
}

/** A position's value from its lots: the one they agree on, else «N разн.» with every value in the tooltip. */
function agreeCell(key: ColumnKey, values: string[], pinned: boolean, className = "") {
  const shown = agreeing(values);
  if (!shown.text) return <td key={key} className={classes("cut stock-dim", className, pinned && "stock-pin")}>—</td>;
  return <td key={key} className={classes("cut", className, shown.many && "stock-dim", pinned && "stock-pin")} title={shown.title}>{shown.text}</td>;
}

/** ГРН of a position: the first one in full, «+N» for the rest; every ГРН in the tooltip. */
function grnCell(key: ColumnKey, lots: WarehouseLot[], pinned: boolean) {
  const grns = [...new Set(lots.map((lot) => lot.grn).filter(Boolean))];
  if (!grns.length) return <td key={key} className={classes("cut stock-dim", pinned && "stock-pin")}>—</td>;
  return <td key={key} className={classes("cut stock-grn", pinned && "stock-pin")} title={grns.join("\n")}>
    {grns[0]}{grns.length > 1 && <span className="stock-more"> +{grns.length - 1}</span>}
  </td>;
}

function ArticleGroup({ group, columns, pinned, expanded, toggle, busy, disabled, mutate, onMove, history, readOnly, canLink }: {
  group: WarehouseGroup;
  columns: ColumnKey[];
  pinned: ColumnKey;
  expanded: Set<string>;
  toggle: (key: string) => void;
  busy: string;
  disabled: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onMove: (lot: WarehouseLot) => void;
  history: boolean;
  readOnly: boolean;
  canLink: boolean;
}) {
  const lotItems = useContext(LotItems);
  return <>
    {group.label && <tr className="stock-group-row"><td colSpan={columns.length}><span className="stock-group-label">{group.label} · {countLabel(group.totals?.articles ?? group.articles.length, ["артикул", "артикула", "артикулов"])}</span>{group.totals && <span className="stock-group-sum"> · {countLabel(group.totals.lots, ["партия", "партии", "партий"])}{group.totals.units.length === 1 ? groupStockText(group.totals, group.totals.units[0]) : ""}</span>}</td></tr>}
    {group.articles.map((item) => {
      const open = expanded.has(item.key);
      return <Fragment key={item.key}>
        <tr className={open ? "stock-article-row open" : "stock-article-row"} title="Партии карточками" onClick={() => toggle(item.key)}>
          {columns.map((key) => articleCell(key, item, open, () => toggle(item.key), key === pinned, lotItems.items))}
        </tr>
        {open && <tr className="stock-cards-row"><td colSpan={columns.length}><div className="lot-cards">
          {item.lots.map((lot) => <LotCard key={`${lot.id}:${lot.version}`} lot={lot} busy={busy === lot.id} disabled={disabled} mutate={mutate} onMove={onMove} history={history} readOnly={readOnly} canLink={canLink} />)}
        </div></td></tr>}
      </Fragment>;
    })}
  </>;
}

const WAREHOUSES = ["ЦАР", "ЖУК"];
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const RACK_CODE = /^(\d{1,2})([A-Z])(\d{1,4})([A-Z])?$/;
const LATIN_TWINS: Record<string, string> = { А: "A", В: "B", С: "C", Е: "E", Н: "H", К: "K", М: "M", О: "O", Р: "P", Т: "T", Х: "X" };

/** «18С3» is typed with a Russian С: the same twins as placeShelfNorm_ / wh05Move norm in SUPPLYMAP. */
function rackCode(value: string) {
  const code = value.replace(/\s+/g, "").toUpperCase().replace(/[АВСЕНКМОРТХ]/g, (letter) => LATIN_TWINS[letter]);
  const match = RACK_CODE.exec(code);
  return match ? { rack: Number(match[1]), shelf: match[2], num: Number(match[3]), slot: match[4] ?? "", code } : null;
}

function lotLine(lot: WarehouseLot) {
  return `${lot.name || lot.article || "—"} · ${lot.balance} ${lot.unit} · ${lot.warehouse}${lot.storage_location ? `, место ${lot.storage_location}` : ", без места"}`;
}

/** SUPPLYMAP wh05Move (PortalApp.html): one window, two modes. «Одну партию» — the whole lot to a new place and/or
    the other warehouse; «Всё место целиком» — everything on a place, within the warehouse (18C3 takes its cells along,
    18C3B only that cell). The target is picked from the rack layout; № defaults to the first free one on the shelf. */
function MoveStockDialog({ preset, lots, busy, mutate, onClose }: {
  preset: WarehouseLot | null;
  lots: WarehouseLot[];
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onClose: () => void;
}) {
  const presetPlace = preset ? rackCode(preset.storage_location) : null;
  const [mode, setMode] = useState<"lot" | "location">(preset ? "lot" : "location");
  const [warehouse, setWarehouse] = useState(preset?.warehouse ?? "ЦАР");
  const [grn, setGrn] = useState(preset?.grn ?? "");
  const [lotId, setLotId] = useState(preset?.id ?? "");
  const [source, setSource] = useState(presetPlace ? `${presetPlace.rack}${presetPlace.shelf}${presetPlace.num}` : "");
  const [layout, setLayout] = useState<WarehouseRackLayout | null>(null);
  const [rack, setRack] = useState("");
  const [shelf, setShelf] = useState("");
  const [num, setNum] = useState("");
  const [slot, setSlot] = useState("");
  const [targetWarehouse, setTargetWarehouse] = useState("");
  const [occupied, setOccupied] = useState<string[]>([]);
  const [nextNumber, setNextNumber] = useState<number | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    warehouseApi.rackLayout().then((result) => setLayout(result.layout), () => setError("Не удалось загрузить раскладку стеллажей"));
  }, []);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  // «Одну партию»: the lot opened from its card, or the one the typed ГРН finds in the loaded list. Several packages
  // under one ГРН — the storekeeper picks the package; otherwise the package never shows.
  const matches = mode === "lot" && !(preset && lotId === preset.id && grn === preset.grn)
    ? lots.filter((item) => item.warehouse === warehouse && item.grn.toUpperCase() === grn.replace(/\s+/g, "").toUpperCase())
    : [];
  const selected = mode !== "lot" ? null
    : preset && lotId === preset.id && grn === preset.grn ? preset
    : matches.find((item) => item.id === lotId) ?? (matches.length === 1 ? matches[0] : null);
  const destination = mode === "lot" ? targetWarehouse || selected?.warehouse || warehouse : warehouse;
  const transfer = mode === "lot" && Boolean(targetWarehouse) && targetWarehouse !== (selected?.warehouse ?? warehouse);

  const from = mode === "location" ? rackCode(source) : null;
  const onPlace = from ? lots.filter((item) => {
    const place = rackCode(item.storage_location);
    return item.warehouse === warehouse && Number(item.balance) > 0 && place !== null && place.rack === from.rack
      && place.shelf === from.shelf && place.num === from.num && (!from.slot || place.slot === from.slot);
  }) : [];

  const shelvesIn = rack && layout ? layout.perRack[rack] ?? layout.shelves : 0;
  const target = rack && shelf && Number(num) >= 1 ? `${rack}${shelf}${Number(num)}${slot}` : "";
  const targetBusy = Boolean(target) && occupied.includes(target);

  // SK-15 placeNextNum_: the first № where nothing lies, on the shelf of the warehouse the lot ends up in.
  useEffect(() => {
    setOccupied([]);
    setNextNumber(null);
    if (!rack || !shelf) { setNum(""); return; }
    let cancelled = false;
    warehouseApi.shelfPlaces(destination, `${rack}${shelf}`).then((result) => {
      if (cancelled) return;
      setOccupied(result.occupied);
      setNextNumber(result.next_number);
      setNum(result.next_number === null ? "" : String(result.next_number));
    }, () => undefined);
    return () => { cancelled = true; };
  }, [destination, rack, shelf]);

  function switchMode(next: "lot" | "location") {
    setMode(next);
    setError("");
    if (next === "location") setTargetWarehouse("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (rack && !target) return setError("Выбери полку и № на полке.");
    try {
      if (mode === "lot") {
        let lot = selected;
        const typed = grn.replace(/\s+/g, "");
        if (!typed) return setError("Укажи ГРН партии.");
        if (!target && !transfer) return setError("Укажи новое место или склад.");
        if (!lot && matches.length > 1) return setError("Под этим ГРН несколько пакетов — выбери пакет.");
        if (!lot) {
          const found = (await warehouseApi.list({ query: typed, includeZero: false, warehouse })).lots
            .filter((item) => item.grn.toUpperCase() === typed.toUpperCase());
          if (found.length > 1) return setError("Под этим ГРН несколько пакетов — найди партию в списке и нажми «Переместить» на её карточке.");
          lot = found[0] ?? null;
        }
        if (!lot) return setError(`Партии ${typed} на складе ${warehouse} нет.`);
        const chosen = lot;
        await mutate(chosen.id, () => warehouseApi.move(chosen.id, chosen.version, target, transfer ? targetWarehouse : ""));
      } else {
        if (!from) return setError("Укажи, откуда: например 18C3.");
        if (!target) return setError("Укажи, куда: стеллаж, полку и № на полке.");
        if (slot && !from.slot) return setError("Место целиком переносится без ячейки — ячейки едут со своими буквами.");
        await mutate("move-location", () => warehouseApi.moveLocation(warehouse, from.code, target));
      }
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Перемещение не выполнено");
    }
  }

  const fromPreset = mode === "lot" && preset !== null && selected === preset;
  return <div className="dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <form className="panel move-dialog" role="dialog" aria-label="Переместить" onSubmit={(event) => void submit(event)}>
      <h2>Переместить</h2>
      {mode === "location" && <p className="page-sub">18C3 уезжает вместе с ячейками (18C3B → 20B5B), 18C3B — только эта ячейка.</p>}
      <div className="seg-tabs move-modes">
        <button type="button" className={mode === "lot" ? "on" : ""} aria-pressed={mode === "lot"} onClick={() => switchMode("lot")}>Одну партию (ГРН)</button>
        <button type="button" className={mode === "location" ? "on" : ""} aria-pressed={mode === "location"} onClick={() => switchMode("location")}>Всё место целиком</button>
      </div>
      {fromPreset && preset ? <div className="move-lot" aria-label="Партия">
        <strong>ГРН {preset.grn}{preset.package ? ` / пакет ${preset.package}` : ""}</strong>
        <span>{lotLine(preset)}</span>
      </div> : <>
        <div className="move-grid two">
          <label>Склад<select aria-label="Склад перемещения" value={warehouse} onChange={(event) => { setWarehouse(event.target.value); setTargetWarehouse(""); setLotId(""); }}>{WAREHOUSES.map((item) => <option key={item}>{item}</option>)}</select></label>
          {mode === "lot"
            ? <label>ГРН партии<input aria-label="ГРН партии" placeholder="OLD.26.0000001" value={grn} onChange={(event) => { setGrn(event.target.value); setLotId(""); }} /></label>
            : <label>Откуда (место)<input aria-label="Исходное место" placeholder="18C3" value={source} onChange={(event) => setSource(event.target.value)} /></label>}
        </div>
        {mode === "lot" && matches.length > 1 && <label>Пакет<select aria-label="Пакет" value={selected?.id ?? ""} onChange={(event) => setLotId(event.target.value)}>
          <option value="">—</option>
          {matches.map((item) => <option key={item.id} value={item.id}>{item.package || "без пакета"} · {item.balance} {item.unit}{item.storage_location ? ` · ${item.storage_location}` : ""}</option>)}
        </select></label>}
        <p className="page-sub move-preview" role="status">{mode === "lot"
          ? selected ? lotLine(selected) : grn.trim() && !matches.length ? "В загруженном списке такой партии нет — проверим на сервере." : ""
          : !source.trim() ? "" : !from ? "Место пишется так: 18C3 или 18C3B."
          : onPlace.length ? `Уедет партий: ${onPlace.length} — ${onPlace.map((item) => `${item.storage_location} · ${item.name || item.article} · ${item.balance} ${item.unit}`).join("; ")}`
          : "В загруженном списке на этом месте ничего нет — проверим на сервере."}</p>
      </>}
      <h3>Куда</h3>
      <div className="move-grid">
        <label>Стеллаж<select aria-label="Стеллаж" value={rack} disabled={!layout} onChange={(event) => { setRack(event.target.value); setShelf(""); }}>
          <option value="">—</option>
          {Array.from({ length: layout?.racks ?? 0 }, (_, index) => String(index + 1)).map((value) => <option key={value}>{value}</option>)}
        </select></label>
        <label>Полка<select aria-label="Полка" value={shelf} disabled={!rack} onChange={(event) => setShelf(event.target.value)}>
          <option value="">—</option>
          {LETTERS.slice(0, shelvesIn).split("").map((value) => <option key={value}>{value}</option>)}
        </select></label>
        <label>№ на полке<input aria-label="№ на полке" type="number" min="1" max="999" step="1" disabled={!shelf} className={targetBusy ? "place-busy" : undefined} title={targetBusy ? BUSY_TITLE : undefined} value={num} onChange={(event) => setNum(event.target.value)} /></label>
        <label>Ячейка<select aria-label="Ячейка" value={slot} disabled={!shelf} className={targetBusy ? "place-busy" : undefined} onChange={(event) => setSlot(event.target.value)}>
          <option value="">—</option>
          {LETTERS.split("").map((value) => <option key={value}>{value}</option>)}
        </select></label>
      </div>
      {target && <p className={targetBusy ? "warning-inline" : "page-sub"} role="status">Новое место: <b>{target}</b>{targetBusy ? ` — ${BUSY_TITLE}` : Number(num) === nextNumber ? ` — первый свободный № на полке ${rack}${shelf}` : ""}</p>}
      {mode === "lot" && <label>Другой склад<select aria-label="Другой склад" value={targetWarehouse} onChange={(event) => setTargetWarehouse(event.target.value)}>
        <option value="">остаётся тот же</option>
        {WAREHOUSES.filter((item) => item !== (selected?.warehouse ?? warehouse)).map((item) => <option key={item} value={item}>{item}</option>)}
      </select></label>}
      {transfer && <p className="page-sub">Партия уедет на {targetWarehouse} целиком: с остатком и историей списаний{target ? "" : ", место останется прежним"}.</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="move-actions">
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
        <button className="primary" disabled={busy}>{busy ? "Перемещаем…" : "Переместить"}</button>
      </div>
    </form>
  </div>;
}

type RowEditField = keyof WarehouseRowEdit;

// SK-7 row edit (SUPPLYMAP Stock05.gs:982-993): reference and passport texts, then the quantities.
const ROW_EDIT_TEXT: Array<[RowEditField, string]> = [
  ["article", "Артикул номенклатуры"], ["maker_article", "Артикул производителя"], ["supplier_article", "Артикул поставщика"],
  ["series", "Серия"], ["name", "Наименование"], ["item_type", "Тип"], ["unit", "Ед. изм."],
  ["purpose", "Назначение"], ["storage_location", "Место хранения"], ["unit_mass_g", "Масса 1 шт, г"],
  ["expiry_date", "Срок годности"], ["manufacturer", "Производитель"], ["description", "Описание"],
  ["storage_conditions", "Условия хранения"], ["datasheet", "Datasheet"], ["certificate", "Сертификат"],
];
const ROW_EDIT_NUMBERS: Array<[RowEditField, string]> = [["inbound", "Приход"], ["written_off", "Списано"], ["reserved", "Резерв"]];

function rowValues(lot: WarehouseLot): Record<RowEditField, string> {
  return Object.fromEntries([...ROW_EDIT_TEXT, ...ROW_EDIT_NUMBERS].map(([field]) => [field, String(lot[field] ?? "")])) as Record<RowEditField, string>;
}

/** Only what differs from the lot. An emptied number means «don't touch», as in SUPPLYMAP. */
function rowChanges(lot: WarehouseLot, values: Record<RowEditField, string>): WarehouseRowEdit {
  const initial = rowValues(lot);
  const changes: WarehouseRowEdit = {};
  for (const [field] of ROW_EDIT_TEXT) {
    if (values[field].trim() !== initial[field].trim()) changes[field] = values[field].trim();
  }
  for (const [field] of ROW_EDIT_NUMBERS) {
    const value = values[field].trim();
    if (value && Number(value) !== Number(initial[field])) changes[field] = value;
  }
  return changes;
}

function LotRowEditForm({ lot, disabled, mutate, onClose, onSaved }: {
  lot: WarehouseLot;
  disabled: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [values, setValues] = useState(() => rowValues(lot));
  const [notice, setNotice] = useState("");
  const number = (field: RowEditField) => Number(values[field].trim() || lot[field] || 0);
  const balance = Math.round((number("inbound") - number("written_off")) * 1_000_000) / 1_000_000;

  function submit(event: FormEvent) {
    event.preventDefault();
    const changes = rowChanges(lot, values);
    if (!Object.keys(changes).length) {
      setNotice("Изменений нет.");
      return;
    }
    setNotice("");
    mutate(lot.id, () => warehouseApi.editRow(lot.id, lot.version, changes))
      .then(onSaved, (reason) => setNotice(dialogError(reason, "Строка не сохранена")));
  }

  return <form className="dialog-form" aria-label="Правка строки" onSubmit={submit}>
    <p className="page-sub">ГРН {lot.grn}{lot.package ? ` · пакет ${lot.package}` : ""} · {lot.warehouse} — ключ строки, не правится. Пустое число — не трогать.</p>
    <div className="move-grid">
    {ROW_EDIT_TEXT.map(([field, label]) => <label key={field}>{label}<input
      type={field === "expiry_date" ? "date" : "text"}
      list={field === "item_type" ? "warehouse-item-types" : undefined}
      inputMode={field === "unit_mass_g" ? "decimal" : undefined}
      required={field === "article" || field === "unit" || field === "purpose"}
      value={values[field]}
      onChange={(event) => { setNotice(""); setValues((current) => ({ ...current, [field]: event.target.value })); }}
    /></label>)}
    {ROW_EDIT_NUMBERS.map(([field, label]) => <label key={field}>{label}<input
      type="number" min="0" step="any" placeholder={String(lot[field] ?? "")}
      value={values[field]}
      onChange={(event) => { setNotice(""); setValues((current) => ({ ...current, [field]: event.target.value })); }}
    /></label>)}
    </div>
    <p className="page-sub" role="status">{notice || `Баланс станет: ${balance} ${lot.unit}`}</p>
    <div className="move-actions">
      <button type="button" className="secondary" onClick={onClose}>Назад</button>
      <button className="primary" disabled={disabled}>Сохранить строку</button>
    </div>
  </form>;
}

// SUPPLYMAP wh05Writeoff / wh05Return: the basis is picked from a list — it tells later whether the stock went to
// production, was scrapped or recounted. A return basis starts with «Возврат» (return_reason on the server).
const WRITEOFF_REASONS = ["Выдача в производство", "Выдача по заявке", "Брак", "Недостача", "Порча", "Инвентаризация"];
const RETURN_REASONS = ["Возврат: не израсходовано", "Возврат: ошибочное списание", "Возврат: замена позиции", "Возврат: из брака (годное)"];

/** Open projects for the «Назначение» / «Откуда» suggestions; a surname is still typed by hand. */
function useProjectChoices() {
  const [projects, setProjects] = useState<string[]>([]);
  useEffect(() => {
    let cancelled = false;
    requestsApi.schema().then((schema) => { if (!cancelled) setProjects(Array.isArray(schema?.projects) ? schema.projects : []); }, () => undefined);
    return () => { cancelled = true; };
  }, []);
  return projects;
}

/** One window for every lot action — the same overlay and panel as «Переместить». */
function LotDialog({ title, lot, wide, onClose, children }: {
  title: string;
  lot: WarehouseLot;
  wide?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  return createPortal(<div className="dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className={wide ? "panel move-dialog wide" : "panel move-dialog"} role="dialog" aria-label={title}>
      <h2>{title}</h2>
      <div className="move-lot" aria-label="Партия">
        <strong>ГРН {lot.grn}{lot.package ? ` / пакет ${lot.package}` : ""}</strong>
        <span>{lotLine(lot)}{Number(lot.written_off) > 0 ? ` · списано ${lot.written_off}` : ""}</span>
      </div>
      {children}
    </div>
  </div>, document.body);
}

/** «Позиция номенклатуры…»: search the item master and link the lot by hand (overrides the receiving chain), or unlink. */
function LotItemDialog({ lot, brief, onClose, onChanged }: { lot: WarehouseLot; brief?: ItemBrief; onClose: () => void; onChanged: () => void }) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<ItemRow[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!query.trim()) { setFound([]); return; }
    let live = true;
    const timer = window.setTimeout(() => {
      purchasingItemsApi.list({ q: query, category: "", hasPrice: false, page: 1 })
        .then((next) => { if (live) setFound(next.items); })
        .catch(() => { if (live) setFound([]); });
    }, 200);
    return () => { live = false; window.clearTimeout(timer); };
  }, [query]);
  async function run(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
      onChanged();
      onClose();
    } catch (reason) {
      setError(dialogError(reason, "Не удалось сохранить связь"));
    }
  }
  return <LotDialog title="Позиция номенклатуры" lot={lot} onClose={onClose}>
    <p className="muted">{brief ? `Сейчас: ${brief.name} (${VIA_LABELS[brief.via]})` : "Позиция не назначена."}</p>
    <input aria-label="Найти позицию номенклатуры" placeholder="Название, артикул" value={query} onChange={(event) => setQuery(event.target.value)} />
    {found.map((row) => <div key={row.id} className="move-lot">
      <strong>{row.name}</strong>
      <span>{row.part_number ?? "без артикула"} · {row.unit}</span>
      <button type="button" className="secondary small" onClick={() => void run(() => purchasingItemsApi.linkLot(lot.id, row.id))}>Связать</button>
    </div>)}
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="move-actions">
      {brief && brief.via !== "chain" && <button type="button" className="link-button danger-text" onClick={() => void run(() => purchasingItemsApi.unlinkLot(lot.id))}>Снять связь</button>}
      <button type="button" className="secondary" onClick={onClose}>Закрыть</button>
    </div>
  </LotDialog>;
}

function dialogError(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

function WriteoffDialog({ lot, disabled, mutate, onClose }: {
  lot: WarehouseLot;
  disabled: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onClose: () => void;
}) {
  const projects = useProjectChoices();
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState(WRITEOFF_REASONS[0]);
  const [purpose, setPurpose] = useState("");
  const [date, setDate] = useState(() => new Date().toLocaleDateString("sv-SE"));
  const [error, setError] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    mutate(lot.id, () => warehouseApi.writeOff(lot.id, lot.version, quantity, purpose.trim(), reason, date || undefined))
      .then(onClose, (reason) => setError(dialogError(reason, "Списание не выполнено")));
  }

  return <LotDialog title="Списать" lot={lot} onClose={onClose}>
    <form className="dialog-form" aria-label="Списание" onSubmit={submit}>
      <div className="move-grid two">
        <label>Количество, {lot.unit || "шт"}<input aria-label="Количество" type="number" min="0.000001" step="any" required autoFocus placeholder={`остаток ${lot.balance}`} value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
        <label>Основание<select aria-label="Основание" value={reason} onChange={(event) => setReason(event.target.value)}>{WRITEOFF_REASONS.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Назначение<SuggestInput aria-label="Назначение списания" options={projects} placeholder="проект или фамилия" required pattern=".*\S.*" title="Номер проекта или фамилия ответственного" value={purpose} onChange={setPurpose} /></label>
        <label>Дата<input aria-label="Дата списания" type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="move-actions">
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
        <button className="primary" disabled={disabled}>Списать</button>
      </div>
    </form>
  </LotDialog>;
}

function ReturnDialog({ lot, disabled, mutate, onClose }: {
  lot: WarehouseLot;
  disabled: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onClose: () => void;
}) {
  const projects = useProjectChoices();
  const [destinations, setDestinations] = useState<WarehouseDestination[]>([]);
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState(RETURN_REASONS[0]);
  const [source, setSource] = useState("");

  // «Откуда» offers where this very lot went; one destination is taken at once. No history (a lot imported
  // from SUPPLYMAP) → the field stays free text over the project list.
  useEffect(() => {
    let cancelled = false;
    warehouseApi.issuedTo(lot.id).then((result) => {
      const list = Array.isArray(result?.destinations) ? result.destinations : [];
      if (cancelled) return;
      setDestinations(list);
      if (list.length === 1) setSource((current) => current || list[0].name);
    }, () => undefined);
    return () => { cancelled = true; };
  }, [lot.id]);
  const chosen = destinations.find((item) => item.name.trim().toLocaleLowerCase("ru") === source.trim().toLocaleLowerCase("ru"));
  const limit = chosen ? String(Math.min(Number(chosen.outstanding), Number(lot.written_off))) : lot.written_off;
  const sourceOptions = [...destinations.map((item) => item.name), ...projects.filter((name) => !destinations.some((item) => item.name.toLocaleLowerCase("ru") === name.toLocaleLowerCase("ru")))];
  const [error, setError] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    mutate(lot.id, () => warehouseApi.returnWriteOff(lot.id, lot.version, quantity, source.trim(), reason))
      .then(onClose, (reason) => setError(dialogError(reason, "Возврат не выполнен")));
  }

  return <LotDialog title="Вернуть на склад" lot={lot} onClose={onClose}>
    <form className="dialog-form" aria-label="Возврат на склад" onSubmit={submit}>
      <div className="move-grid two">
        <label>Вернуть, {lot.unit || "кол-во"}<input aria-label="Количество возврата" type="number" min="0.000001" max={limit} step="any" required autoFocus placeholder={`не больше ${limit}`} title={chosen ? `Ушло на ${chosen.name}: ${limit} ${lot.unit}` : `Списано ${lot.written_off} ${lot.unit}`} value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
        <label>Основание<select aria-label="Основание возврата" value={reason} onChange={(event) => setReason(event.target.value)}>{RETURN_REASONS.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Откуда<SuggestInput aria-label="Откуда возврат" options={sourceOptions} placeholder="проект или фамилия" required pattern=".*\S.*" value={source} onChange={setSource} /></label>
      </div>
      {destinations.length > 0 && <div className="return-from" role="group" aria-label="Куда выдавали">
        {destinations.map((item) => <button type="button" key={item.name} className={`secondary small${item === chosen ? " on" : ""}`} aria-pressed={item === chosen} onClick={() => setSource(item.name)}>{item.name} · осталось {item.outstanding} {item.unit || lot.unit}</button>)}
      </div>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="move-actions">
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
        <button className="primary" disabled={disabled}>Вернуть</button>
      </div>
    </form>
  </LotDialog>;
}

function LotEditDialog({ lot, disabled, mutate, onClose }: {
  lot: WarehouseLot;
  disabled: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onClose: () => void;
}) {
  const [article, setArticle] = useState(lot.article);
  const [name, setName] = useState(lot.name);
  const [series, setSeries] = useState(lot.series);
  const [place, setPlace] = useState(lot.storage_location);
  const [editingRow, setEditingRow] = useState(false);
  const [error, setError] = useState("");
  const run = (operation: () => Promise<unknown>) => {
    setError("");
    mutate(lot.id, operation).then(onClose, (reason) => setError(dialogError(reason, "Правка не сохранена")));
  };

  return <LotDialog title="Правка" lot={lot} wide={editingRow} onClose={onClose}>
    <LotDocuments lot={lot} />
    {editingRow ?<LotRowEditForm lot={lot} disabled={disabled} mutate={mutate} onClose={() => setEditingRow(false)} onSaved={onClose} /> : <>
      <form className="dialog-form" aria-label="Карточка партии" onSubmit={(event) => {
        event.preventDefault();
        run(() => warehouseApi.editIdentity(lot.id, lot.version, article.trim(), name.trim(), series.trim()));
      }}>
        <div className="move-grid three">
          <label>Артикул номенклатуры<input required value={article} onChange={(event) => setArticle(event.target.value)} /></label>
          <label>Наименование<input value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>Серия<input value={series} onChange={(event) => setSeries(event.target.value)} /></label>
        </div>
        <div className="move-actions"><button className="secondary" disabled={disabled}>Сохранить карточку</button></div>
      </form>
      <form className="dialog-form" aria-label="Место партии" onSubmit={(event) => {
        event.preventDefault();
        run(() => warehouseApi.correctLocation(lot.id, lot.version, place.trim()));
      }}>
        <div className="move-grid three">
          <label>Место как есть<input aria-label="Место как есть" title="Ручная правка: любой текст (12C7, 12C7B, A-01…), пусто — без места" placeholder="без места" value={place} onChange={(event) => setPlace(event.target.value)} /></label>
          <button className="secondary dialog-inline-action" disabled={disabled}>Записать место</button>
        </div>
      </form>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="move-actions">
        <button type="button" className="link-button" disabled={disabled} onClick={() => setEditingRow(true)}>Править строку</button>
        <button type="button" className="secondary" onClick={onClose}>Закрыть</button>
      </div>
    </>}
  </LotDialog>;
}

/** «Документы» of a lot, read-only: the УПД, invoice and payment document of its delivery, the names they give it, the
 *  stored УПД. Nothing known — nothing shown (old lots entered before «Поставка»). */
function LotDocuments({ lot }: { lot: WarehouseLot }) {
  const rows = lotDocuments(lot);
  if (!rows.length) return null;
  return <section className="lot-docs" aria-label="Документы">
    <h3>Документы</h3>
    <dl>{rows.map((row) => <Fragment key={row.label}>
      <dt>{row.label}</dt>
      <dd>{row.href ? <a href={row.href} target="_blank" rel="noreferrer">{row.value}</a> : row.value}</dd>
    </Fragment>)}</dl>
  </section>;
}

/** «История» партии (SUPPLYMAP openStock05Log(grn)): the stock journal of this ГРН and package, newest first. */
function LotHistoryDialog({ lot, onClose }: { lot: WarehouseLot; onClose: () => void }) {
  const [journal, setJournal] = useState<WarehouseJournal | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    warehouseApi.journal(lot.grn).then(
      (loaded) => { if (!cancelled) setJournal(loaded); },
      (reason) => { if (!cancelled) setError(dialogError(reason, "Не удалось загрузить историю")); },
    );
    return () => { cancelled = true; };
  }, [lot.grn]);

  const rows = (journal?.rows ?? []).filter((row) => {
    const [grn, pkg = ""] = row.lot.split(" / ");
    return grn.trim().toUpperCase() === lot.grn.toUpperCase() && (!pkg || !lot.package || pkg.trim() === lot.package);
  });
  return <LotDialog title="История" lot={lot} wide onClose={onClose}>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!error && !journal && <div className="empty">Загрузка…</div>}
    {!error && journal && (rows.length ? <div className="table-wrap history-table"><table className="grid compact" aria-label="История партии">
      <thead><tr><th>Когда</th><th>Что</th><th>Подробно</th><th>Склад</th><th>Кто</th></tr></thead>
      <tbody>{rows.map((row, index) => <tr key={index}>
        <td>{new Date(row.occurred_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })}</td>
        <td>{row.kind}</td><td className="text">{row.event}</td><td>{row.warehouse}</td><td>{row.who}</td>
      </tr>)}</tbody>
    </table></div> : <div className="empty">По этой партии записей нет.</div>)}
    <div className="move-actions"><button type="button" className="secondary" onClick={onClose}>Закрыть</button></div>
  </LotDialog>;
}

function LotCard({ lot, busy, disabled, mutate, onMove, history, readOnly, canLink }: {
  lot: WarehouseLot;
  busy: boolean;
  disabled: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onMove: (lot: WarehouseLot) => void;
  history: boolean;
  readOnly: boolean;
  /** «Справочник» на правку: кнопка «В базу данных…». */
  canLink: boolean;
}) {
  const purposeText = usePurposeLabel();
  // Every action is a window over the stock, as the buttons on a SUPPLYMAP partition card (− / + / ✎ / История).
  const [dialog, setDialog] = useState<"" | "writeoff" | "return" | "edit" | "history" | "item" | "link" | "label">("");
  const close = () => setDialog("");
  const writtenOff = Number(lot.written_off) > 0;
  const passport = passportLine(lot);
  const itemsCtx = useContext(LotItems);
  const brief = itemsCtx.items[lot.id];

  return (
    <article className={busy ? "lot-card busy" : "lot-card"} aria-label={`Партия ${lot.grn}`}>
      {/* A wide card (owner 26.10: «пора увеличить ширину карточки»): quantity and place, the lot's lines beside them,
          the actions in one row under them. */}
      <div className="lot-card-head">
        <div className="lot-card-main">
          <div><span className="lot-card-big">{lot.available} {lot.unit}</span><small>свободно · остаток {lot.balance}{Number(lot.reserved) > 0 ? ` · резерв ${lot.reserved}` : ""}</small></div>
          <div><span className="lot-card-big">{lot.storage_location || "без места"}</span><small>{lot.warehouse}</small></div>
        </div>
        <div className="lot-card-info">
          <p className="lot-card-meta">
            <span>ГРН {lot.grn}{lot.package ? ` / ${lot.package}` : ""}</span>
            {lot.purpose && <span>{purposeText(lot.purpose)}</span>}
            {writtenOff && <span>списано {lot.written_off}</span>}
            {Number(lot.reserved) > 0 && lot.reserve_note && <span title={lot.reserve_note}>{lot.reserve_note.replace(/^резерв: /, "")}</span>}
          </p>
          {passport && <p className="lot-card-passport" aria-label="Паспорт партии">{passport}</p>}
          {receiptLine(lot.receipt) && <p className="lot-card-meta"><small className="muted">{receiptLine(lot.receipt)}</small></p>}
          {brief && <p className="lot-card-meta"><small className="muted lot-item-chip" title={`Связь: ${VIA_LABELS[brief.via]}`}>Позиция: {brief.name}</small></p>}
        </div>
      </div>
      <div className="lot-card-actions">
        {!readOnly && <>
          <button className="secondary small" disabled={disabled} title="Снять количество с партии" onClick={() => setDialog("writeoff")}>− Списать</button>
          {writtenOff && <button className="secondary small" disabled={disabled} title={`Списано ${lot.written_off} ${lot.unit}`} onClick={() => setDialog("return")}>Вернуть на склад</button>}
          <button className="secondary small" disabled={disabled} onClick={() => onMove(lot)}>{busy ? "Сохраняем…" : "Переместить"}</button>
          <button className="secondary small" disabled={disabled} onClick={() => setDialog("edit")}>✎ Правка</button>
          <button className="secondary small" title="Предпросмотр и печать этикетки ГРН" onClick={() => setDialog("label")}>Этикетка…</button>
        </>}
        {!readOnly && <button className="secondary small" disabled={disabled} onClick={() => setDialog("item")}>Позиция номенклатуры…</button>}
        {canLink && <button className="secondary small" title="Привязать к записи базы или создать запись" onClick={() => setDialog("link")}>В базу данных…</button>}
        {history && <button className="secondary small" onClick={() => setDialog("history")}>История</button>}
        {!readOnly && <UndoLast lot={lot} disabled={disabled} mutate={mutate} />}
      </div>
      {!readOnly && dialog === "writeoff" && <WriteoffDialog lot={lot} disabled={disabled} mutate={mutate} onClose={close} />}
      {!readOnly && dialog === "return" && writtenOff && <ReturnDialog lot={lot} disabled={disabled} mutate={mutate} onClose={close} />}
      {!readOnly && dialog === "edit" && <LotEditDialog lot={lot} disabled={disabled} mutate={mutate} onClose={close} />}
      {canLink && dialog === "link" && <StockLinkDialog lot={lot} onClose={close} />}
      {history && dialog === "history" && <LotHistoryDialog lot={lot} onClose={close} />}
      {!readOnly && dialog === "label" && <LotDialog title="Этикетка" lot={lot} onClose={close}>
        <LotLabel key={lot.version} lotId={lot.id} />
        <div className="move-actions"><button type="button" className="secondary" onClick={close}>Закрыть</button></div>
      </LotDialog>}
      {!readOnly && dialog === "item" && <LotItemDialog lot={lot} brief={brief} onClose={close} onChanged={itemsCtx.reload} />}
    </article>
  );
}
