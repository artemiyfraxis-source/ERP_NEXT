import { Fragment, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { SuggestInput } from "./SuggestInput";
import {
  receivingApi,
  requestsApi,
  warehouseApi,
  type WarehouseAddResult,
  type WarehouseDestination,
  type WarehouseJournal,
  type WarehouseLot,
  type WarehouseLotPassport,
  type WarehouseManualItem,
  type WarehouseRackLayout,
  type WarehouseRowEdit,
} from "./api";
import { createPortal } from "react-dom";
import { openPrintSheet } from "./print";
import { BUSY_TITLE, PlaceHint, usePlaceHint } from "./WarehousePlaceHint";
import { downloadStockTable, ItemTypesPanel, RackLayoutPanel, StockImportPanel, StockJournalPanel } from "./WarehouseReferences";
import {
  chooserColumns, ColumnChooser, columnTitle, defaultLayout, moveColumn, readLayouts, shownColumns as visibleColumns, writeLayouts,
  type ColumnKey, type ColumnLayout, type TableKind,
} from "./WarehouseColumns";

const EMPTY_PASSPORT: WarehouseLotPassport = {
  mass_1: "", mass_10: "", mass_100: "", expiry_date: null,
  manufacturer: "", description: "", storage_conditions: "", datasheet: "", certificate: "",
};

/** Lot passport as one line for the operations row (SK-6); empty fields are left out. */
function passportLine(lot: WarehouseLot) {
  return passportParts(lot.unit_mass_g ? `Масса 1 шт: ${lot.unit_mass_g} г` : "", lot);
}

/** The same line for a quick-entry row: the mass as typed (1, 10 or 100 pieces). */
function manualPassportLine(item: WarehouseManualItem) {
  const mass = ([["mass_1", 1], ["mass_10", 10], ["mass_100", 100]] as const)
    .filter(([field]) => String(item[field] ?? "").trim())
    .map(([field, count]) => `Масса ${count} шт: ${item[field]} г`).join(" · ");
  return passportParts(mass, item);
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
 *  (SUPPLYMAP portalApplyAccess_ 'view' drops b.edit, portalStock05Guard_ refuses writes). */
export function WarehousePage({ admin = false, readOnly = false }: { admin?: boolean; readOnly?: boolean } = {}) {
  const [lots, setLots] = useState<WarehouseLot[]>([]);
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
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
  const [assigned, setAssigned] = useState<WarehouseAddResult["assigned"]>([]);
  const [copied, setCopied] = useState("");
  // Type suggestions (SK-14): the dictionary + the canonical types already on the stock.
  const [typeChoices, setTypeChoices] = useState<string[]>([]);
  // WH-6: печатный «Лист перемещения на склад» для принятых партий без места — тот же механизм,
  // что и FF-8 (frontend/src/print.ts): backend строит PrintSheet, окно печати открывает print.ts.
  const [putawayBusy, setPutawayBusy] = useState(false);
  // Своё сообщение: загрузка склада (через 250 мс после открытия) сбрасывает общую ошибку и стёрла бы его.
  const [putawayError, setPutawayError] = useState("");
  const requestVersion = useRef(0);
  const groupedLots = useMemo(() => warehouseGroups(lots, groupBy, sort), [lots, groupBy, sort]);
  // «Колонки»: each table has its own order and set of columns, the viewer changes both (remembered in this browser).
  // The name is always there; the column the rows are grouped by is the group header already, not repeated in every row.
  const [layouts, setLayouts] = useState(readLayouts);
  const kind: TableKind = flat ? "flat" : "grouped";
  const layout = layouts[kind];
  const groupColumn = !flat && (groupBy === "series" || groupBy === "type") ? groupBy : null;
  const shownColumns = visibleColumns(layout, groupColumn);
  const chooserKeys = chooserColumns(layout, groupColumn);
  // The leftmost column stays in view when the table is scrolled sideways.
  const pinned = shownColumns[0];
  const changeLayout = (change: (current: ColumnLayout) => ColumnLayout) => setLayouts((current) => ({ ...current, [kind]: change(current[kind]) }));
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

  async function loadAllLots() {
    const loaded: WarehouseLot[] = [];
    let cursor: string | undefined;
    let total = 0;
    do {
      const page = await warehouseApi.list({ after: cursor, query, includeZero, warehouse });
      loaded.push(...page.lots);
      total = page.total;
      cursor = page.next_cursor ?? undefined;
    } while (cursor);
    return { lots: [...new Map(loaded.map((lot) => [lot.id, lot])).values()], total };
  }

  async function reload() {
    const version = ++requestVersion.current;
    setLoading(true);
    setError("");
    try {
      const result = await loadAllLots();
      if (version !== requestVersion.current) return;
      setLots(result.lots);
      setTotal(result.total);
    } catch (reason) {
      if (version !== requestVersion.current) return;
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить склад");
    } finally {
      if (version === requestVersion.current) setLoading(false);
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
  }, [query, warehouse, includeZero]);

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
      await operation();
      const result = await loadAllLots();
      setLots(result.lots);
      setTotal(result.total);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Складская операция не выполнена");
      throw reason;
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="page stock-page">
      <header className="page-head">
        <h1>Склад</h1>
        <span className="page-count">{total}</span>
        {/* The daily actions stay in sight; the reference tools sit behind «Ещё» (every one still one click away). */}
        <div className="page-actions">
          <button className="secondary" disabled={loading || Boolean(busy)} onClick={() => void reload()}>{loading ? "Обновляем…" : "Обновить"}</button>
          {!readOnly && <button className="secondary" disabled={putawayBusy} onClick={() => void openPutawaySheet()}>Лист перемещения</button>}
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
            ...(admin ? [{ label: "Журнал склада", run: () => setPanel((value) => value === "journal" ? "" : "journal") }] : []),
          ]} />
          {!readOnly && <button className="primary" disabled={loading || Boolean(busy)} onClick={() => setAdding((value) => !value)}>+ Внести позиции</button>}
        </div>
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {putawayError && <div className="error-banner" role="alert">{putawayError}</div>}
      {/* Wording from SUPPLYMAP «Склад 0.5» (PortalApp.html wh-hint). */}
      {readOnly && <p className="page-sub">Склад открыт вам только на просмотр.</p>}
      <div className="filters table-toolbar stock-toolbar">
        <input className="search stock-search" aria-label="Поиск по складу" placeholder="поиск: резистор 6-1 …" value={query} onChange={(event) => setQuery(event.target.value)} />
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
            onReset={() => changeLayout(() => defaultLayout(kind))}
          />
        </div>
      </div>
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
      {assigned.length > 0 && <section className="panel" aria-label="Записано на склад">
        <div className="manual-stock-head"><h2>Записано на склад: {assigned.length}</h2><button type="button" className="link-button" onClick={() => { setAssigned([]); setCopied(""); }}>Закрыть</button></div>
        <p className="page-sub">Номера партий, присвоенные строкам. Слева — номер строки, как она стояла на экране.</p>
        <div className="table-wrap"><table className="grid compact"><thead><tr><th>#</th><th>ГРН</th><th>Артикул</th><th>Кол-во</th><th>Место</th><th>Склад</th></tr></thead>
          <tbody>{assigned.map((row) => <tr key={row.id}><td>{row.i + 1}</td><td><strong>{row.grn}</strong></td><td>{row.article}</td><td className="num">{row.quantity} {row.unit}</td><td>{row.storage_location || "—"}</td><td>{row.warehouse}</td></tr>)}</tbody>
        </table></div>
        <div className="header-actions"><button type="button" className="secondary small" onClick={() => {
          // Копия — табуляцией: вставляется в таблицу/этикетку столбцами (PortalApp.html wa05ShowGrns_).
          const text = assigned.map((row) => [row.grn, row.article, `${row.quantity} ${row.unit}`, row.storage_location, row.warehouse].join("\t")).join("\n");
          void navigator.clipboard.writeText(text).then(() => setCopied("Скопировано."), () => setCopied("Браузер не дал скопировать — выдели таблицу мышью."));
        }}>Скопировать</button><span className="page-sub" role="status">{copied}</span></div>
      </section>}
      {adding && <ManualStockForm
        busy={Boolean(busy) || loading}
        knownLots={lots}
        typeChoices={typeChoices}
        onCancel={() => setAdding(false)}
        onAdded={async (items) => {
          requestVersion.current += 1;
          setBusy("manual");
          setError("");
          try {
            const result = await warehouseApi.add(items);
            if (result.assigned.length) { setAssigned(result.assigned); setCopied(""); }
            // Rows with an error stay in the form; the rest are entered (SK-10, stock05Add_).
            if (!result.errors.length) setAdding(false);
            if (result.added) await reload();
            return result;
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : "Не удалось внести позиции");
            return null;
          } finally {
            setBusy("");
          }
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
        <table className="grid wide compact stock-table" aria-label="Таблица склада">
          <colgroup>{shownColumns.map((key) => <col key={key} className={`stock-col-${key}`} />)}</colgroup>
          <thead><tr>{shownColumns.map((key) => <th key={key} className={classes(columnClass(key), key === pinned && "stock-pin")}>{columnTitle(key, true)}</th>)}</tr></thead>
          <tbody>{lots.map((lot) => <tr key={lot.id}>{shownColumns.map((key) => flatCell(key, lot, key === pinned))}</tr>)}</tbody>
        </table>
      </div> : <div className="table-wrap" ref={wrapRef}>
        <table className="grid wide compact stock-table">
          <colgroup>{shownColumns.map((key) => <col key={key} className={`stock-col-${key}`} />)}</colgroup>
          <thead><tr>{shownColumns.map((key) => <th key={key} className={classes(columnClass(key), key === pinned && "stock-pin")} aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : undefined}>
            <button type="button" className="th-sort" onClick={() => setSort((current) => ({ key: key as SortKey, desc: current.key === key ? !current.desc : false }))}>{columnTitle(key, false)}{sort.key === key ? (sort.desc ? " ▼" : " ▲") : ""}</button>
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
            history={admin}
            readOnly={readOnly}
          />)}</tbody>
        </table>
      </div>}
      {!lots.length && !loading && <div className="empty">{includeZero ? "Склад пуст." : "Позиций с остатком нет. Включите «показывать нулевые», чтобы увидеть израсходованные партии."}</div>}
    </section>
  );
}

type WarehouseArticle = {
  key: string;
  article: string;
  lots: WarehouseLot[];
};

type WarehouseGroup = {
  key: string;
  label: string;
  articles: WarehouseArticle[];
};

const warehouseCollator = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });

function articleKey(lot: WarehouseLot) {
  const identity = (lot.article || lot.name).trim().replace(/\s+/g, " ").toLocaleLowerCase("ru");
  return identity || `\u0001грн ${lot.grn}/${lot.package}`;
}

function shelf(location: string) {
  return location.replace(/\s+/g, "").toUpperCase().match(/^(\d+[A-ZА-Я])/)?.[1] ?? location;
}

type SortKey = Exclude<ColumnKey, "available" | "purpose">;

function columnClass(key: ColumnKey) {
  return key === "name" ? "stock-name" : key === "balance" || key === "lots" || key === "available" ? "num" : undefined;
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

function sortValue(item: WarehouseArticle, key: SortKey): string | number {
  switch (key) {
    case "type": return distinct(item.lots, "item_type");
    case "grn": return distinct(item.lots, "grn");
    case "balance": return Number(quantity(item.lots, "balance"));
    case "lots": return item.lots.length;
    case "place": return distinct(item.lots, "storage_location");
    case "series": return distinct(item.lots, "series");
    case "name": return distinct(item.lots, "name");
    case "warehouse": return distinct(item.lots, "warehouse");
    default: return item.article;
  }
}

function compareArticles(left: WarehouseArticle, right: WarehouseArticle, sort: { key: SortKey; desc: boolean }) {
  const a = sortValue(left, sort.key);
  const b = sortValue(right, sort.key);
  const order = typeof a === "number" && typeof b === "number" ? a - b : warehouseCollator.compare(String(a), String(b));
  return (sort.desc ? -order : order) || warehouseCollator.compare(left.key, right.key);
}

function warehouseGroups(lots: WarehouseLot[], groupBy: "" | "type" | "place" | "name" | "series" | "grn", sort: { key: SortKey; desc: boolean }): WarehouseGroup[] {
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
      return {
        key: `${groupBy}:${groupKey}`,
        label: !groupBy ? "" : groupKey || ({ type: "Без типа", place: "Без места", name: "Без наименования", series: "Без серии", grn: "Без ГРН" })[groupBy],
        articles: [...articleMap.entries()]
          .map(([key, articleLots]) => ({ key: `${groupBy}:${groupKey}:${key}`, article: articleLots[0].article || articleLots[0].name, lots: articleLots }))
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

function quantity(lots: WarehouseLot[], field: "balance" | "available") {
  return String(Math.round(lots.reduce((sum, lot) => sum + Number(lot[field]), 0) * 1_000_000) / 1_000_000);
}

/** A cell cut to its column with «…»; the full text is the tooltip. */
function cutCell(key: ColumnKey, text: string, pinned: boolean, className = "") {
  return <td key={key} className={classes("cut", className, pinned && "stock-pin")} title={text || undefined}>{text}</td>;
}

/** One lot of «Таблица склада», cell by cell in the order of the visible columns. */
function flatCell(key: ColumnKey, lot: WarehouseLot, pinned: boolean) {
  switch (key) {
    case "name": return <td key={key} className={classes("stock-name", pinned && "stock-pin")} title={lot.name || undefined}>{lot.name || "—"}</td>;
    case "article": return cutCell(key, lot.article || "—", pinned);
    case "grn": return cutCell(key, `${lot.grn}${lot.package ? ` · ${lot.package}` : ""}`, pinned, "stock-grn");
    case "balance": return <td key={key} className={classes("num", pinned && "stock-pin")}><strong>{lot.balance} {lot.unit}</strong></td>;
    case "available": return <td key={key} className={classes("num", pinned && "stock-pin")}>{lot.available}</td>;
    case "place": return cutCell(key, lot.storage_location || "—", pinned);
    case "warehouse": return cutCell(key, lot.warehouse, pinned);
    case "purpose": return cutCell(key, lot.purpose || "—", pinned);
    case "series": return cutCell(key, lot.series || "—", pinned);
    case "type": return cutCell(key, lot.item_type || "—", pinned);
    default: return null;
  }
}

/** One article (all its lots) of the grouped table, cell by cell in the order of the visible columns. */
function articleCell(key: ColumnKey, item: WarehouseArticle, open: boolean, toggle: () => void, pinned: boolean) {
  switch (key) {
    case "name": {
      const name = distinct(item.lots, "name") || item.article || "Без наименования";
      return <td key={key} className={classes("stock-name", pinned && "stock-pin")}><button className="link-button table-link" title={name} aria-expanded={open} aria-label={`${open ? "Скрыть" : "Показать"} партии (${item.lots.length})`} onClick={(event) => { event.stopPropagation(); toggle(); }}>{name}</button></td>;
    }
    case "article": return cutCell(key, item.article || "—", pinned);
    case "grn": return cutCell(key, distinct(item.lots, "grn"), pinned, "stock-grn");
    case "balance": {
      const unit = distinct(item.lots, "unit");
      return <td key={key} className={classes("num", pinned && "stock-pin")}><strong>{quantity(item.lots, "balance")}{unit ? ` ${unit}` : ""}</strong></td>;
    }
    case "lots": return <td key={key} className={classes("num", pinned && "stock-pin")}>{item.lots.length}</td>;
    case "place": return <td key={key} className={classes("cut", pinned && "stock-pin")} title={distinct(item.lots, "storage_location") || undefined}>{places(item.lots)}</td>;
    case "warehouse": return cutCell(key, distinct(item.lots, "warehouse"), pinned);
    case "series": return cutCell(key, distinct(item.lots, "series"), pinned);
    case "type": return cutCell(key, distinct(item.lots, "item_type"), pinned);
    default: return null;
  }
}

function ArticleGroup({ group, columns, pinned, expanded, toggle, busy, disabled, mutate, onMove, history, readOnly }: {
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
}) {
  return <>
    {group.label && <tr className="stock-group-row"><td colSpan={columns.length}><span className="stock-group-label">{group.label} · {group.articles.length}</span></td></tr>}
    {group.articles.map((item) => {
      const open = expanded.has(item.key);
      return <Fragment key={item.key}>
        <tr className={open ? "stock-article-row open" : "stock-article-row"} title="Партии карточками" onClick={() => toggle(item.key)}>
          {columns.map((key) => articleCell(key, item, open, () => toggle(item.key), key === pinned))}
        </tr>
        {open && <tr className="stock-cards-row"><td colSpan={columns.length}><div className="lot-cards">
          {item.lots.map((lot) => <LotCard key={`${lot.id}:${lot.version}`} lot={lot} busy={busy === lot.id} disabled={disabled} mutate={mutate} onMove={onMove} history={history} readOnly={readOnly} />)}
        </div></td></tr>}
      </Fragment>;
    })}
  </>;
}

function ManualStockForm({ busy, knownLots, typeChoices, onCancel, onAdded }: {
  busy: boolean;
  knownLots: WarehouseLot[];
  typeChoices: string[];
  onCancel: () => void;
  onAdded: (items: WarehouseManualItem[]) => Promise<WarehouseAddResult | null>;
}) {
  // Rows put aside with «В список» and rows the server did not accept (with the reason).
  const [rows, setRows] = useState<Array<{ item: WarehouseManualItem; error: string }>>([]);
  const [report, setReport] = useState("");
  const [passport, setPassport] = useState<WarehouseLotPassport>(EMPTY_PASSPORT);
  const passportField = (field: keyof WarehouseLotPassport) => ({
    value: passport[field] ?? "",
    onChange: (event: { target: { value: string } }) => {
      const value = event.target.value;
      setPassport((current) => ({ ...current, [field]: field === "expiry_date" ? value || null : value }));
    },
  });
  const [warehouse, setWarehouse] = useState("ЦАР");
  const [article, setArticle] = useState("");
  const [series, setSeries] = useState("");
  const [name, setName] = useState("");
  const [itemType, setItemType] = useState("");
  const [purpose, setPurpose] = useState("Склад");
  const [unit, setUnit] = useState("шт");
  const [quantity, setQuantity] = useState("");
  const [storageLocation, setStorageLocation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [articleSource, setArticleSource] = useState("");

  useEffect(() => {
    const currentArticle = article.trim();
    if (!currentArticle) {
      setArticleSource("");
      return;
    }

    const key = currentArticle.replace(/\s+/g, " ").toLocaleLowerCase("ru");
    const known = knownLots.find((lot) => lot.article.trim().replace(/\s+/g, " ").toLocaleLowerCase("ru") === key);
    if (known) {
      setName((current) => current || known.name);
      setSeries((current) => current || known.series);
      setItemType((current) => current || known.item_type);
      setUnit((current) => current || known.unit);
      setArticleSource("Подставлено по уже внесённой партии");
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      void warehouseApi.articleInfo(currentArticle).then(({ info }) => {
        if (cancelled || !info) return;
        setName((current) => current || info.name);
        setSeries((current) => current || info.series);
        setItemType((current) => current || info.item_type);
        setArticleSource(`Подставлено: ${info.source}. Проверьте и при необходимости поправьте.`);
      }).catch(() => undefined);
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [article, knownLots]);

  const currentItem = (): WarehouseManualItem | null => {
    if (!article.trim() && !name.trim() && !quantity.trim()) return null;
    return {
      warehouse,
      article: article.trim(),
      series: series.trim(),
      name: name.trim(),
      item_type: itemType.trim(),
      purpose: purpose.trim(),
      unit: unit.trim(),
      quantity,
      storage_location: storageLocation.trim(),
      // Only what was filled: an empty passport keeps the plain quick-entry payload.
      ...Object.fromEntries(Object.entries(passport).filter(([, value]) => String(value ?? "").trim())),
    };
  };
  // The next row keeps the warehouse, purpose, unit and place — a pass over the stock goes shelf by shelf.
  const clearRow = () => {
    setArticle(""); setName(""); setSeries(""); setItemType(""); setQuantity("");
    setPassport(EMPTY_PASSPORT); setArticleSource("");
  };
  // «Править»: the listed row goes back into the form; an unfinished row in the form takes its place in the list.
  const editRow = (index: number) => {
    const { item } = rows[index];
    const current = currentItem();
    setRows((existing) => existing.flatMap((row, other) => other !== index ? [row] : current ? [{ item: current, error: "" }] : []));
    setWarehouse(item.warehouse); setArticle(item.article); setSeries(item.series); setName(item.name);
    setItemType(item.item_type); setPurpose(item.purpose); setUnit(item.unit); setQuantity(item.quantity);
    setStorageLocation(item.storage_location);
    setPassport(Object.fromEntries(Object.keys(EMPTY_PASSPORT).map((field) => {
      const key = field as keyof WarehouseLotPassport;
      return [key, item[key] ?? EMPTY_PASSPORT[key]];
    })) as unknown as WarehouseLotPassport);
    setArticleSource(""); setReport("");
  };
  const anyRefused = rows.some((row) => row.error);
  const pending = rows.length + (currentItem() ? 1 : 0);
  const placeHint = usePlaceHint(warehouse, storageLocation,
    rows.filter((row) => row.item.warehouse === warehouse).map((row) => row.item.storage_location));

  return (
    <form className="panel manual-stock-form" noValidate onSubmit={async (event) => {
      event.preventDefault();
      if (submitting) return;
      const current = currentItem();
      const items = [...rows.map((row) => row.item), ...(current ? [current] : [])];
      if (!items.length) {
        setReport("Добавьте хотя бы одну позицию");
        return;
      }
      setSubmitting(true);
      try {
        const result = await onAdded(items);
        if (!result) return;
        if (current) clearRow();
        const refused = result.errors.map(({ i, error }) => ({ item: items[i], error: `${i + 1} — ${error}` }));
        setRows(refused);
        // SUPPLYMAP wording (PortalApp.html: «Записано: N. Не приняты строки (M): i+1 — ошибка; …»).
        setReport(`Записано: ${result.added}` + (refused.length
          ? `. Не приняты строки (${refused.length}): ${refused.map((row) => row.error).join("; ")}`
          : ". Можно продолжать."));
      } finally {
        setSubmitting(false);
      }
    }}>
      <div className="manual-stock-head"><h2>Быстрый ввод</h2><button type="button" className="link-button" onClick={onCancel}>Отмена</button></div>
      <div className="manual-stock-grid">
        <label>Артикул<input value={article} onChange={(event) => {
          setArticleSource("");
          setArticle(event.target.value);
        }} /></label>
        <label>Наименование<input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>Серия<input value={series} onChange={(event) => setSeries(event.target.value)} /></label>
        <label>Тип<SuggestInput options={typeChoices} value={itemType} onChange={setItemType} /></label>
        <label>Количество<input aria-label="Количество для внесения" type="number" min="0.000001" step="any" value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
        <label>Единица<input value={unit} onChange={(event) => setUnit(event.target.value)} /></label>
        <label>Склад<select value={warehouse} onChange={(event) => setWarehouse(event.target.value)}><option value="ЦАР">ЦАР</option><option value="ЖУК">ЖУК</option></select></label>
        <label>Назначение<input value={purpose} onChange={(event) => setPurpose(event.target.value)} /></label>
        <label>Место хранения<input aria-label="Место хранения для внесения" placeholder="1F4" className={placeHint.busy ? "place-busy" : undefined} title={placeHint.busy ? BUSY_TITLE : undefined} value={storageLocation} onChange={(event) => setStorageLocation(event.target.value)} /></label>
      </div>
      <PlaceHint {...placeHint} place={storageLocation} onPick={setStorageLocation} />
      <details className="manual-stock-passport">
        <summary>Паспорт партии — необязательно</summary>
        <p className="page-sub">Масса: заполните любое одно поле — мелочь точнее взвесить десятком или сотней, хранится масса одной штуки.</p>
        <div className="manual-stock-grid">
          <label>Масса 1 шт, г<input aria-label="Масса 1 шт, г" inputMode="decimal" {...passportField("mass_1")} /></label>
          <label>Масса 10 шт, г<input aria-label="Масса 10 шт, г" inputMode="decimal" {...passportField("mass_10")} /></label>
          <label>Масса 100 шт, г<input aria-label="Масса 100 шт, г" inputMode="decimal" {...passportField("mass_100")} /></label>
          <label>Срок годности<input aria-label="Срок годности" type="date" {...passportField("expiry_date")} /></label>
          <label>Производитель<input aria-label="Производитель" {...passportField("manufacturer")} /></label>
          <label>Описание<input aria-label="Описание" {...passportField("description")} /></label>
          <label>Условия хранения<input aria-label="Условия хранения" {...passportField("storage_conditions")} /></label>
          <label>Datasheet<input aria-label="Datasheet" {...passportField("datasheet")} /></label>
          <label>Сертификат<input aria-label="Сертификат" {...passportField("certificate")} /></label>
        </div>
      </details>
      {articleSource && <p className="page-sub" role="status">{articleSource}</p>}
      {rows.length > 0 && <table className="grid compact manual-stock-rows" aria-label="Позиции к внесению">
        <thead><tr><th>Артикул</th><th>Наименование</th><th>Серия</th><th>Тип</th><th className="num">Кол-во</th><th>Место</th><th>Склад</th><th>Назначение</th><th>Паспорт</th>{anyRefused && <th>Не принята</th>}<th /></tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>
          <td>{row.item.article || "—"}</td>
          <td>{row.item.name || "—"}</td>
          <td>{row.item.series || "—"}</td>
          <td>{row.item.item_type || "—"}</td>
          <td className="num">{String(row.item.quantity).trim() ? `${row.item.quantity} ${row.item.unit}` : "—"}</td>
          <td>{row.item.storage_location || "—"}</td>
          <td>{row.item.warehouse}</td>
          <td>{row.item.purpose || "—"}</td>
          <td className="manual-stock-passport-cell">{manualPassportLine(row.item) || "—"}</td>
          {anyRefused && <td className="danger-text">{row.error}</td>}
          <td className="manual-stock-row-actions">
            <button type="button" className="link-button" aria-label={`Править строку ${index + 1}`} onClick={() => editRow(index)}>Править</button>
            <button type="button" className="link-button" aria-label="Убрать строку" onClick={() => setRows((current) => current.filter((_, other) => other !== index))}>×</button>
          </td>
        </tr>)}</tbody>
      </table>}
      {report && <p className="page-sub" role="log">{report}</p>}
      <div className="manual-stock-actions">
        <button type="button" className="secondary" disabled={busy || submitting} onClick={() => {
          const current = currentItem();
          if (!current) return;
          setRows((existing) => [...existing, { item: current, error: "" }]);
          clearRow();
          setReport("");
        }}>+ В список</button>
        <button className="primary" disabled={busy || submitting}>{submitting ? "Вносим…" : pending > 1 ? `Внести (${pending})` : "Внести"}</button>
      </div>
    </form>
  );
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
  ["article", "Артикул"], ["series", "Серия"], ["name", "Наименование"], ["item_type", "Тип"], ["unit", "Ед. изм."],
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
    {editingRow ? <LotRowEditForm lot={lot} disabled={disabled} mutate={mutate} onClose={() => setEditingRow(false)} onSaved={onClose} /> : <>
      <form className="dialog-form" aria-label="Карточка партии" onSubmit={(event) => {
        event.preventDefault();
        run(() => warehouseApi.editIdentity(lot.id, lot.version, article.trim(), name.trim(), series.trim()));
      }}>
        <div className="move-grid three">
          <label>Артикул<input required value={article} onChange={(event) => setArticle(event.target.value)} /></label>
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

function LotCard({ lot, busy, disabled, mutate, onMove, history, readOnly }: {
  lot: WarehouseLot;
  busy: boolean;
  disabled: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onMove: (lot: WarehouseLot) => void;
  history: boolean;
  readOnly: boolean;
}) {
  // Every action is a window over the stock, as the buttons on a SUPPLYMAP partition card (− / + / ✎ / История).
  const [dialog, setDialog] = useState<"" | "writeoff" | "return" | "edit" | "history">("");
  const close = () => setDialog("");
  const writtenOff = Number(lot.written_off) > 0;
  const passport = passportLine(lot);

  return (
    <article className={busy ? "lot-card busy" : "lot-card"} aria-label={`Партия ${lot.grn}`}>
      <div className="lot-card-main">
        <div><span className="lot-card-big">{lot.balance} {lot.unit}</span><small>остаток{lot.available !== lot.balance ? ` · доступно ${lot.available}` : ""}</small></div>
        <div><span className="lot-card-big">{lot.storage_location || "без места"}</span><small>{lot.warehouse}</small></div>
      </div>
      <p className="lot-card-meta">
        <span>ГРН {lot.grn}{lot.package ? ` / ${lot.package}` : ""}</span>
        {lot.purpose && <span>{lot.purpose}</span>}
        {writtenOff && <span>списано {lot.written_off}</span>}
      </p>
      {passport && <p className="lot-card-passport" aria-label="Паспорт партии">{passport}</p>}
      {(!readOnly || history) && <div className="lot-card-actions">
        {!readOnly && <>
          <button className="secondary small" disabled={disabled} title="Снять количество с партии" onClick={() => setDialog("writeoff")}>− Списать</button>
          {writtenOff && <button className="secondary small" disabled={disabled} title={`Списано ${lot.written_off} ${lot.unit}`} onClick={() => setDialog("return")}>Вернуть на склад</button>}
          <button className="secondary small" disabled={disabled} onClick={() => onMove(lot)}>{busy ? "Сохраняем…" : "Переместить"}</button>
          <button className="secondary small" disabled={disabled} onClick={() => setDialog("edit")}>✎ Правка</button>
        </>}
        {history && <button className="secondary small" onClick={() => setDialog("history")}>История</button>}
      </div>}
      {!readOnly && dialog === "writeoff" && <WriteoffDialog lot={lot} disabled={disabled} mutate={mutate} onClose={close} />}
      {!readOnly && dialog === "return" && writtenOff && <ReturnDialog lot={lot} disabled={disabled} mutate={mutate} onClose={close} />}
      {!readOnly && dialog === "edit" && <LotEditDialog lot={lot} disabled={disabled} mutate={mutate} onClose={close} />}
      {history && dialog === "history" && <LotHistoryDialog lot={lot} onClose={close} />}
    </article>
  );
}
