import { Fragment, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { urlParam } from "./navigation";
import {
  ApiError,
  failureText,
  manualRatePrompt,
  procurementApi,
  type ApplyOrderDocumentInput,
  type InvoiceUpload,
  type KpRowInput,
  type ManualRatePrompt,
  type MoneyDriver,
  type OrderDocumentKind,
  type ProcurementLine,
  type ProcurementTraceabilityInput,
  type SkippedRequestLine,
  type SupplierOrder,
} from "./api";
import { DataTable, readSaved, writeSaved, type DataColumn } from "./DataTable";
import { ProcurementBySupplier, ProcurementSheetView } from "./ProcurementSheet";
import { ProcurementInWorkTable } from "./ProcurementInWorkTable";
import { ProcurementKpi } from "./ProcurementKpi";
import { ProcurementToday } from "./ProcurementToday";
import { projectLabel, useInternalProjects } from "./internalProjects";
import { useStageOption } from "./StageOptions";
import { PurchasingCatalog } from "./PurchasingCatalog";
import { PurchasingDocChain } from "./PurchasingDocChain";
import { PurchasingDocSearch } from "./PurchasingDocSearch";
import { PurchasingInvoices } from "./PurchasingInvoices";
import { PurchasingItems } from "./PurchasingItems";
import { PurchasingRfq } from "./PurchasingRfq";
import { openRfq } from "./rfqLinks";
import { PurchasingSettings } from "./PurchasingSettings";
import { PurchasingSuppliers } from "./PurchasingSuppliers";
import { PurchasingUpd } from "./PurchasingUpd";
import { UpdInvoicesBlock } from "./UpdInvoicesBlock";
import { ProcurementStockPicker, waitsForGrn } from "./ProcurementStockPicker";
import {
  BackorderStage, COMPACT_BELOW, OrderStageBlock, OtherStage, StageHeading, orderGroups, orderStage,
  type BulkState, type OrderGroup, type RowAction,
} from "./ProcurementStages";
import { runInPool } from "./bulk";
import { formatDate } from "./format";
import { StatusChip } from "./StatusChip";
import type { LineEditing } from "./ProcurementCellEdit";
import { LineDocumentsForm, readBase64 } from "./ProcurementLineDocuments";

// ЕД.ИЗМ (факт) — выпадающий список, как в SUPPLYMAP (фолбэк REQUEST_FORM_FIELDS['ЕД.ИЗМ']); сервер отдаёт его в /meta.
const DEFAULT_UNITS = ["шт", "кг", "м", "л", "уп", "компл"];

type ProcurementTab = "today" | "queues" | "sheet" | "suppliers" | "kpi" | "rfq" | "directory" | "items" | "catalog" | "settings"
  | "upd" | "invoices" | "chain" | "docsearch";

// «Мои позиции» (владелец, 05.10) — один блок «В работе»: строки к запросу (с «Готов к запросу») по поставщикам,
// под ними лента заказов — заказ не переезжает между блоками, его статус (Жду счёт → Ждёт оплаты → В пути →
// Карантин → Принято) меняется на месте. «Согласование» — только дозавоз; обычная строка, поставленная туда
// до отключения этапа, и любой новый статус — в «Прочие позиции».
const IN_WORK_STATUSES = new Set(["В работе", "Готов к запросу"]);
const ORDER_STATUSES = new Set(["Жду счет", "В оплату", "В пути", "Карантин", "Готово"]);
const APPROVAL = "Согласование";
const isBackorderLine = (line: ProcurementLine) => line.status === APPROVAL && line.is_backorder;

// После «Взять в работу» сервер дозаполняет пустые поля в фоне (~3 с): очередь перечитывается дважды.
const AUTOFILL_REFRESH_MS = [4000, 10000];

type BulkBlock = "inwork" | "orders";

/** Блоки «Моих позиций» в фиксированном порядке; «Блоки» выбирает, какие показывать (помнится в браузере). */
const BLOCKS = [
  { key: "inwork", title: "В работе" },
  { key: "awaiting", title: "Жду счёт" },
  { key: "topay", title: "Ждёт оплаты" },
  { key: "transit", title: "В пути" },
  { key: "quarantine", title: "Карантин" },
  { key: "accepted", title: "Принято за 30 дней" },
  { key: "backorder", title: "Дозавоз" },
  { key: "other", title: "Прочие позиции" },
] as const;
const BLOCKS_KEY = "procurement-blocks";

/** Прежний блок «Оплачено» (ключ `paid`) разделён на три: скрытый ранее — скрываются все три. */
export function migrateHiddenBlocks(hidden: string[]): string[] {
  if (!hidden.includes("paid")) return hidden;
  return [...new Set([...hidden.filter((key) => key !== "paid"), "transit", "quarantine", "accepted"])];
}

/** Строка подходит под поиск по секции: ID, наименование, поставщик, № заказа, № счёта. */
const lineMatches = (line: ProcurementLine, query: string) =>
  [line.legacy_id, line.calculated_name, line.supplier, line.order_number, line.invoice_number]
    .some((value) => (value ?? "").toLowerCase().includes(query));

/** Блоки заказов «Моих позиций» по стадии; «Принято» сначала свёрнут — он для справки. */
const ORDER_BLOCKS = [
  { stage: "awaiting", title: "Жду счёт", hint: "Запрос поставщику отправлен — вставьте счёт или КП", initiallyCollapsed: false },
  { stage: "topay", title: "Ждёт оплаты", hint: "Счёт есть — отметьте «Оплачено», когда заплатили", initiallyCollapsed: false },
  { stage: "transit", title: "В пути", hint: "Оплачено, едет к нам; частично принятые заказы остаются здесь", initiallyCollapsed: false },
  { stage: "quarantine", title: "Карантин", hint: "Приёмка нашла несоответствие по строке — ждёт решения", initiallyCollapsed: false },
  { stage: "accepted", title: "Принято за 30 дней", hint: "Все строки заказа приняты на склад; показаны принятые за последние 30 дней", initiallyCollapsed: true },
] as const;

/** «Мои позиции» читаются все страницы подряд, чтобы заказ не разрезало границей страницы; не больше стольких. */
const MAX_MINE_PAGES = 50;

/** Скачивает PDF запроса поставщику файлом «Запрос PO-012.pdf» (сервер отдаёт его вложением по `?download=1`). */
function downloadRequestDocument(order: SupplierOrder) {
  const link = document.createElement("a");
  link.href = `/api/procurement/supplier-requests/${encodeURIComponent(order.order_number)}/document?download=1`;
  link.download = `Запрос ${order.order_number}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/** Свежие строки встают на место старых, новые — в конец; строк, которых в ответе нет, это не касается. */
function mergeLines(current: ProcurementLine[], fresh: ProcurementLine[]) {
  const byId = new Map(fresh.map((line) => [line.id, line]));
  const known = new Set(current.map((line) => line.id));
  return [...current.map((line) => byId.get(line.id) ?? line), ...fresh.filter((line) => !known.has(line.id))];
}

const availableColumns: DataColumn<ProcurementLine>[] = [
  { key: "legacy_id", title: "ID", value: (line) => line.legacy_id, render: (line) => <><strong>{line.legacy_id}</strong><small>рев. {line.revision}</small></> },
  { key: "name", title: "Позиция", value: (line) => line.calculated_name, render: (line) => <>{line.calculated_name}<small>{line.designation}</small></> },
  { key: "project", title: "Проект", value: (line) => projectLabel(line.project_reference), render: (line) => <>{projectLabel(line.project_reference) || "—"}<small>{line.delivery_point}</small></> },
  { key: "planned", title: "Срок", value: (line) => line.planned_date, render: (line) => <>{formatDate(line.planned_date)}<small>{line.priority}</small></> },
  { key: "priority", title: "Приоритет", value: (line) => line.priority, filter: true, hidden: true },
  { key: "qty", title: "Кол-во", value: (line) => `${line.calculated_quantity} ${line.calculated_unit}` },
  // Поставщик не обязателен для «Взять в работу», но без него запрос поставщику не сформируется — это видно сразу.
  {
    key: "supplier", title: "Поставщик", value: (line) => line.supplier ?? "", filter: true,
    render: (line) => line.supplier || <span className="tone-warn">нет (запрос не сформируется)</span>,
  },
];

const inWorkColumns: DataColumn<ProcurementLine>[] = [
  { key: "legacy_id", title: "ID", value: (line) => line.legacy_id, render: (line) => <><strong>{line.legacy_id}</strong><small>{projectLabel(line.project_reference) || line.designation}</small></> },
  { key: "name", title: "Позиция", value: (line) => line.calculated_name, render: (line) => <>{line.calculated_name}<small>{line.actual_quantity ?? line.calculated_quantity} {line.actual_unit ?? line.calculated_unit}</small></> },
  { key: "buyer", title: "Закупщик", value: (line) => line.buyer },
  { key: "status", title: "Статус", value: (line) => line.status, filter: true, render: (line) => <StatusChip status={line.status} /> },
  { key: "supplier", title: "Поставщик · заказ", value: (line) => line.supplier, render: (line) => <>{line.supplier || "—"}<small>{line.order_number || ""}</small></> },
  { key: "eta", title: "Ожидается", value: (line) => line.expected_arrival },
];

const TABS: readonly ProcurementTab[] = [
  "today", "queues", "sheet", "suppliers", "kpi", "rfq", "directory", "items", "catalog", "settings", "upd", "invoices", "chain",
  "docsearch",
];
/** `?tab=` (the global search lands on a tab); an unknown or switched-off tab falls back to the default below. */
function initialTab(): ProcurementTab | null {
  const value = urlParam("tab");
  return TABS.find((tab) => tab === value) ?? null;
}

/** `readOnly` — «режим просмотра» (ID-6): free lines and every buyer's lines in work are read, nothing is taken
 *  or changed (SUPPLYMAP «Лист закупки — просмотр, что едет»: portalApplyAccess_ drops edit and canWork). */
export function ProcurementPage({ readOnly = false, buyerName = "" }: { readOnly?: boolean; buyerName?: string } = {}) {
  // Stage options: «Сегодня» (first, and the default only when on; the buyer's own list, so not in view mode),
  // «Показатели», and the purchasing hybrid (Roller_Buyer): requests for quotes, supplier and item directories, settings.
  useInternalProjects(); // option projects.internal: «ВП-001 · Название» in the project columns
  const todayOn = useStageOption("procurement.today") && !readOnly;
  const kpiOn = useStageOption("procurement.kpi");
  const rfqOn = useStageOption("procurement.rfq");
  const directoryOn = useStageOption("references.suppliers");
  const itemsOn = useStageOption("references.items");
  const priceBookOn = useStageOption("references.price_book");
  const updOn = useStageOption("documents.upd");
  const chainOn = useStageOption("documents.chain");
  const settingsOn = rfqOn || directoryOn || itemsOn;
  const shown: Record<string, boolean> = {
    today: todayOn, kpi: kpiOn, rfq: rfqOn, directory: directoryOn, items: itemsOn, catalog: priceBookOn, settings: settingsOn,
    upd: updOn, invoices: chainOn, chain: chainOn, docsearch: chainOn,
  };
  const [picked, setPicked] = useState<ProcurementTab | null>(initialTab);
  // Своих и свободных позиций нет — очереди пусты: первым открывается «Весь лист», а не пустой экран (аудит 10.10, СН-1).
  const [autoOpened, setAutoOpened] = useState(false);
  const pickedByHand = useRef(picked !== null); // a tab from the URL counts as chosen by hand
  const [updFocus, setUpdFocus] = useState<string | undefined>(undefined); // a UPD number elsewhere → its card in «УПД»
  const pick = (next: ProcurementTab) => {
    pickedByHand.current = true; setAutoOpened(false); setPicked(next);
    if (next === "upd") setUpdFocus(undefined); // the tab itself opens the list, not the last card
  };
  const openUpd = updOn ? (id: string) => { pick("upd"); setUpdFocus(id); } : undefined;
  const openSheetWhenIdle = () => {
    if (pickedByHand.current) return;
    setAutoOpened(true);
    setPicked("sheet");
  };
  // The sheet opened by itself (empty queues while the options were still loading) gives way to «Сегодня».
  const tab: ProcurementTab = picked && shown[picked] === false ? "queues"
    : autoOpened && todayOn ? "today" : picked ?? (todayOn ? "today" : "queues");
  const groups: Array<Array<[ProcurementTab, string, boolean]>> = [
    [["today", "Сегодня", todayOn], ["queues", "Очереди", true], ["sheet", "Весь лист", true], ["suppliers", "По поставщикам", true], ["kpi", "Показатели", kpiOn]],
    [["rfq", "Запросы КП", rfqOn]],
    [["directory", "Поставщики", directoryOn], ["items", "Номенклатура", itemsOn], ["catalog", "Каталог закупок", priceBookOn], ["settings", "Настройки", settingsOn]],
    [["upd", "УПД", updOn], ["invoices", "Счета", chainOn], ["chain", "Цепочка документов", chainOn], ["docsearch", "Поиск по документам", chainOn]],
  ];
  const visibleGroups = groups.map((group) => group.filter(([, , on]) => on)).filter((group) => group.length);
  return (
    <>
      <header className="page-head"><h1>Лист закупки</h1></header>
      {readOnly && <p className="page-sub">Лист закупки открыт вам только на просмотр: что свободно и что едет.</p>}
      <div className="seg-tabs" role="tablist" aria-label="Разделы закупки">
        {visibleGroups.map((group, index) => (
          <Fragment key={group[0][0]}>
            {index > 0 && <span className="tab-sep" aria-hidden="true" />}
            {group.map(([key, label]) => (
              <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : undefined} onClick={() => pick(key)}>{label}</button>
            ))}
          </Fragment>
        ))}
      </div>
      {tab === "sheet" && autoOpened && <p className="page-sub procurement-auto-note">У вас нет позиций в работе и свободных позиций нет — показан весь лист. Свои позиции появятся во вкладке «Очереди».</p>}
      {tab === "today" && <ProcurementToday onOpenQueues={() => pick("queues")} onOpenRfq={rfqOn ? (id) => openRfq(id) : undefined} />}
      {tab === "kpi" && <ProcurementKpi />}
      {tab === "rfq" && <PurchasingRfq readOnly={readOnly} />}
      {tab === "directory" && <PurchasingSuppliers readOnly={readOnly} onOpenUpd={openUpd} />}
      {tab === "items" && <PurchasingItems readOnly={readOnly} onOpenUpd={openUpd} />}
      {tab === "catalog" && <PurchasingCatalog readOnly={readOnly} onOpenUpd={openUpd} />}
      {tab === "settings" && <PurchasingSettings readOnly={readOnly} />}
      {tab === "upd" && <PurchasingUpd readOnly={readOnly} focusId={updFocus}
        extraBlocks={chainOn ? (documentId) => <UpdInvoicesBlock documentId={documentId} readOnly={readOnly} /> : undefined} />}
      {tab === "invoices" && <PurchasingInvoices readOnly={readOnly} onOpenUpd={openUpd} />}
      {tab === "chain" && <PurchasingDocChain readOnly={readOnly} onOpenUpd={openUpd} />}
      {tab === "docsearch" && <PurchasingDocSearch readOnly={readOnly} onOpenUpd={openUpd} />}
      {tab === "queues" && <ProcurementQueues readOnly={readOnly} buyerName={buyerName} onIdle={openSheetWhenIdle} onOpenSheet={() => pick("sheet")} />}
      {tab === "sheet" && <section className="page"><ProcurementSheetView buyerName={readOnly ? "" : buyerName} /></section>}
      {tab === "suppliers" && <section className="page"><ProcurementBySupplier /></section>}
    </>
  );
}

function ProcurementQueues({ readOnly, buyerName, onIdle, onOpenSheet }: { readOnly: boolean; buyerName: string; onIdle?: () => void; onOpenSheet?: () => void }) {
  const [units, setUnits] = useState<string[]>(DEFAULT_UNITS);
  // «Заказы: Все · Активные · Скрыть» — у каждого закупщика свой выбор в этом браузере.
  // Только что взятые строки → их версия при взятии: пока сервер не дозаполнил строку, на ней «заполняется…».
  const [autofill, setAutofill] = useState<Map<string, number>>(new Map());
  const timers = useRef<number[]>([]);
  const [bulk, setBulk] = useState<(BulkState & { block: BulkBlock }) | null>(null);
  // «Внести КП» по очереди: первый заказ открыт, остальные ждут.
  const [kpQueue, setKpQueue] = useState<string[]>([]);
  const [kpTotal, setKpTotal] = useState(0);
  const [available, setAvailable] = useState<ProcurementLine[]>([]);
  const [mine, setMine] = useState<ProcurementLine[]>([]);
  const [availableCursor, setAvailableCursor] = useState<string | null>(null);
  const [mineCursor, setMineCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // Предупреждения сервера к последнему действию («<ID> — нет цены: ВАЛЮТА, …»): живут рядом с итогом, до следующего действия.
  const [warnings, setWarnings] = useState<string[]>([]);
  const say = (text: string, warns: string[] = []) => { setNotice(text); setWarnings(warns); };
  const [busyId, setBusyId] = useState("");
  // Строки, что в запрос не попали, и почему: остаются на экране, пока их не закроют.
  const [skippedRequests, setSkippedRequests] = useState<{ text: string }[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [sectionQuery, setSectionQuery] = useState("");
  const [hiddenBlocks, setHiddenBlocks] = useState<string[]>(() => migrateHiddenBlocks(readSaved(BLOCKS_KEY).hidden ?? []));
  const [choosingBlocks, setChoosingBlocks] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const idleChecked = useRef(false);
  const [loadingMore, setLoadingMore] = useState<"available" | "mine" | "">("");
  const kpReference = kpQueue[0] ?? "";
  const loadingQueue = refreshing || Boolean(loadingMore);
  const interactionLocked = Boolean(busyId) || loadingQueue;
  const secondQueue = readOnly ? procurementApi.inWork : procurementApi.mine;

  async function reload(afterMutation = false) {
    if (loadingQueue || (busyId && !afterMutation)) return;
    setRefreshing(true);
    setError("");
    try {
      const [availableResult, mineResult] = await Promise.all([
        procurementApi.available(),
        secondQueue(),
      ]);
      let mineLines = mineResult.lines;
      let cursor = mineResult.next_cursor;
      // «Мои позиции» группируются по заказам: страницы дочитываются все (кроме режима просмотра).
      for (let pages = 1; !readOnly && cursor && pages < MAX_MINE_PAGES; pages += 1) {
        const page = await secondQueue(cursor);
        const known = new Set(mineLines.map((line) => line.id));
        mineLines = [...mineLines, ...page.lines.filter((line) => !known.has(line.id))];
        cursor = page.next_cursor;
      }
      setAvailable(availableResult.lines);
      setMine(mineLines);
      setAvailableCursor(availableResult.next_cursor);
      setMineCursor(cursor);
      setLoaded(true);
      if (!idleChecked.current) {
        idleChecked.current = true;
        if (!availableResult.lines.length && !mineLines.length) onIdle?.();
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Ошибка загрузки закупки");
    } finally {
      setRefreshing(false);
    }
  }

  async function loadMore(queue: "available" | "mine", cursor: string) {
    if (interactionLocked) return;
    setLoadingMore(queue);
    setError("");
    try {
      const result = queue === "available" ? await procurementApi.available(cursor) : await secondQueue(cursor);
      if (queue === "available") {
        setAvailable((current) => [
          ...current,
          ...result.lines.filter((line) => !current.some((item) => item.id === line.id)),
        ]);
        setAvailableCursor(result.next_cursor);
      } else {
        setMine((current) => [
          ...current,
          ...result.lines.filter((line) => !current.some((item) => item.id === line.id)),
        ]);
        setMineCursor(result.next_cursor);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Ошибка загрузки закупки");
    } finally {
      setLoadingMore("");
    }
  }

  useEffect(() => {
    void reload();
    if (!readOnly) procurementApi.meta().then((meta) => setUnits(meta.units)).catch(() => undefined);
    return () => timers.current.forEach((timer) => window.clearTimeout(timer));
  }, []);

  /** Взятые строки сервер дозаполняет в фоне (поставщик по ссылке, наименование на сайте, изготовитель, страна):
   *  «Мои позиции» перечитываются через ~4 и ~10 с. Свежие значения встают только в нетронутые ячейки —
   *  набранное в «В работе» лежит поверх строки и не стирается. */
  function watchAutofill(taken: ProcurementLine[]) {
    if (!taken.length) return;
    setAutofill((current) => new Map([...current, ...taken.map((line): [string, number] => [line.id, line.version])]));
    AUTOFILL_REFRESH_MS.forEach((delay, index) => {
      const last = index === AUTOFILL_REFRESH_MS.length - 1;
      timers.current.push(window.setTimeout(() => void refreshTaken(taken.map((line) => line.id), last), delay));
    });
  }

  async function refreshTaken(ids: string[], last: boolean) {
    let fresh: ProcurementLine[] = [];
    try {
      fresh = (await secondQueue()).lines;
      setMine((current) => mergeLines(current, fresh));
    } catch { /* фоновое обновление: следующее или «Обновить» */ }
    const versions = new Map(fresh.map((line) => [line.id, line.version]));
    setAutofill((current) => {
      const next = new Map(current);
      for (const id of ids) {
        const version = versions.get(id);
        if (last || (version !== undefined && version !== current.get(id))) next.delete(id);
      }
      return next;
    });
  }

  /** `portalTakeInWork`: выбранные «Новый» строки — одним запросом. */
  async function takeMany(lines: ProcurementLine[]) {
    if (interactionLocked || !lines.length) return;
    setBusyId("take-many");
    setError("");
    try {
      const result = await procurementApi.take(lines.map((line) => line.id));
      const skipped = result.skipped ?? [];
      say(`Взято в работу: ${result.lines.length}` + (skipped.length ? `; пропущено (уже в работе или дальше): ${skipped.join(", ")}` : ""));
      watchAutofill(result.lines);
      await reload(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось взять строки");
      throw reason;
    } finally {
      setBusyId("");
    }
  }

  /** Окно документа: файл счёта или КП → распознанные № и дата, цены строк заказа (сервер ничего не пишет). */
  function recognizeDocument(group: OrderGroup, file: File, kind: OrderDocumentKind) {
    return procurementApi.recognizeOrderDocument(group.number, file, kind);
  }

  /** «Вставить счёт» / «Вставить КП» / «Заменить счёт»: проверенные цены — все строки заказа или ни одной.
   *  409 — набор строк устарел: очередь перечитывается, окно остаётся открытым со свежими версиями строк. */
  async function applyDocument(group: OrderGroup, input: ApplyOrderDocumentInput, replace: boolean): Promise<void> {
    setBusyId(`order:${group.number}`);
    setError("");
    try {
      const result = await procurementApi.applyOrderDocument(group.number, input);
      const count = input.lines.length;
      say(input.kind === "kp"
        ? `${group.number}: цены из КП внесены, строк: ${count}`
        : replace ? `${group.number}: счёт заменён, строк: ${count}` : `${group.number}: счёт передан в оплату, строк: ${count}`,
      result.warnings ?? []);
      await reload(true);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        await reload(true);
        throw new Error("Строки заказа изменились — список обновлён. Проверьте цены и подтвердите ещё раз");
      }
      throw reason;
    } finally {
      setBusyId("");
    }
  }

  /** «Оплачено» по заказу: все его строки «В оплату» → «В пути». Вернёт вопрос про курс, если ЦБ не ответил. */
  async function paidOrder(group: OrderGroup, manualRate?: string): Promise<ManualRatePrompt | null> {
    if (interactionLocked) return null;
    setBusyId(`order:${group.number}`);
    setError("");
    const rows = group.lines.filter((line) => line.status === "В оплату");
    try {
      await procurementApi.orderPaid(group.number, rows.map((line) => ({ line_id: line.id, version: line.version })), manualRate);
      say(`${group.number}: оплачено, строк в пути: ${rows.length}`);
      await reload(true);
      return null;
    } catch (reason) {
      const prompt = manualRatePrompt(reason);
      if (prompt) return prompt;
      if (reason instanceof ApiError && reason.status === 409) {
        await reload(true);
        setError("Строки заказа изменились — список обновлён. Проверьте заказ и нажмите «Оплачено» ещё раз");
      } else {
        setError(failureText(reason, "Оплата не проведена"));
      }
      return null;
    } finally {
      setBusyId("");
    }
  }

  async function take(line: ProcurementLine) {
    if (interactionLocked) return;
    setBusyId(line.id);
    setError("");
    try {
      const result = await procurementApi.take([line.id]);
      say(result.skipped?.length ? `${line.legacy_id} пропущена: уже в работе или дальше` : `${line.legacy_id} взята в работу`);
      watchAutofill(result.lines);
      await reload(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось взять строку");
    } finally {
      setBusyId("");
    }
  }

  async function advance(
    line: ProcurementLine,
    operation: () => Promise<unknown>,
    message: string,
  ) {
    if (interactionLocked) return;
    setBusyId(line.id);
    setError("");
    try {
      const result = await operation();
      const warns = (result as { warnings?: unknown } | null | undefined)?.warnings;
      say(message, Array.isArray(warns) ? warns.filter((item): item is string => typeof item === "string") : []);
      await reload(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Переход закупки не выполнен");
      throw reason;
    } finally {
      setBusyId("");
    }
  }

  /** Массовое действие блока по выбранным строкам: не больше 4 запросов сразу, ход «12 из 40» под заголовком
   *  блока, в конце — итог и отказы по строкам; потом очередь перечитывается. */
  async function runMany(block: BulkBlock, label: string, rows: ProcurementLine[], work: (line: ProcurementLine) => Promise<unknown>, doneWord: string) {
    if (interactionLocked || !rows.length) return;
    setBusyId(`bulk:${block}`);
    setError("");
    setBulk({ block, label, done: 0, total: rows.length });
    try {
      const failures = await runInPool(rows, work, (done) => setBulk((current) => (current?.block === block ? { ...current, done } : current)));
      setBulk({
        block, label, done: rows.length, total: rows.length,
        summary: `${doneWord}: ${rows.length - failures.length} из ${rows.length}`,
        failures: failures.map(({ item, error }) => `${item.legacy_id}: ${failureText(error, "не выполнено")}`),
      });
      await reload(true);
    } finally {
      setBusyId("");
    }
  }

  function cancelMany(lines: ProcurementLine[]) {
    if (!lines.length || !window.confirm(`Отменить строк: ${lines.length}? Отмена окончательная.`)) return Promise.resolve();
    return runMany("inwork", "Отменяем строки", lines, (line) => procurementApi.cancelLine(line.id, line.version), "Отменено");
  }

  function reconcileMany(lines: ProcurementLine[]) {
    return runMany("orders", "Сверяем курс с ЦБ", lines, (line) => procurementApi.reconcileRate(line.id, line.version), "Курс сверен");
  }

  /** Запросы поставщикам по строкам «Готов к запросу» (группа поставщика): заказ на поставщика, PDF каждого
   *  запроса скачивается файлом «Запрос PO-012.pdf»; пропущенные строки — списком с причиной. */
  async function buildRequests(lines?: ProcurementLine[]) {
    if (interactionLocked) return;
    setBusyId("supplier-requests");
    setError("");
    setSkippedRequests([]);
    try {
      const result = await procurementApi.buildSupplierRequests(lines?.map((line) => line.id));
      const notSaved: string[] = [];
      for (const order of result.orders) {
        try { downloadRequestDocument(order); } catch { notSaved.push(order.order_number); }
      }
      const parts = [`Сформировано запросов: ${result.orders.length}`];
      if (result.orders.length) parts.push(notSaved.length ? `файл не сохранился: ${notSaved.join(", ")}` : "PDF запросов скачаны файлами");
      if (result.other_buyer) parts.push(`строк других закупщиков не тронуто: ${result.other_buyer}`);
      say(parts.join("; "));
      const names = new Map([...mine, ...(lines ?? [])].map((line) => [line.id, line.legacy_id]));
      setSkippedRequests(result.skipped.map((item: SkippedRequestLine | string) => {
        const entry = typeof item === "string" ? { line_id: item, reason: "" } : item;
        const name = names.get(entry.line_id) ?? entry.line_id;
        return { text: entry.reason ? `${name}: ${entry.reason}` : name };
      }));
      await reload(true);
    } catch (reason) {
      setError(failureText(reason, "Запросы поставщикам не сформированы"));
    } finally {
      setBusyId("");
    }
  }

  // Один поиск по всей секции: фильтрует каждый блок и его счётчики. Заказ целиком остаётся, если подходит
  // любая его строка — иначе заказ «переехал» бы между стадиями.
  const needle = sectionQuery.trim().toLowerCase();
  const found = (line: ProcurementLine) => !needle || lineMatches(line, needle);
  const stage = (status: string) => mine.filter((line) => line.status === status && found(line));
  // «В работе» — сначала строки в работе, под ними «Готов к запросу» (они ждут запроса поставщику).
  const inWorkBlock = [...stage("В работе"), ...stage("Готов к запросу")];
  // Заказы — раздельными блоками по стадии: «Жду счёт», «Ждёт оплаты», «В пути», «Карантин», «Принято» (свёрнут сначала).
  const orders = useMemo(() => orderGroups(mine.filter((line) => ORDER_STATUSES.has(line.status))), [mine]);
  const shownOrders = useMemo(
    () => (needle ? orders.filter((group) => group.lines.some((line) => lineMatches(line, needle))) : orders),
    [orders, needle],
  );
  const ordersAt = (stage: string) => shownOrders.filter((group) => orderStage(group) === stage);
  const backorders = mine.filter((line) => isBackorderLine(line) && found(line));
  const other = mine.filter((line) => !IN_WORK_STATUSES.has(line.status) && !ORDER_STATUSES.has(line.status) && !isBackorderLine(line) && found(line));
  const blockCount: Record<string, number> = {
    inwork: inWorkBlock.length, awaiting: ordersAt("awaiting").length, topay: ordersAt("topay").length,
    transit: ordersAt("transit").length, quarantine: ordersAt("quarantine").length, accepted: ordersAt("accepted").length,
    backorder: backorders.length, other: other.length,
  };
  const hiddenWithData = BLOCKS.filter((block) => hiddenBlocks.includes(block.key) && blockCount[block.key] > 0);
  const showBlock = (key: string) => !hiddenBlocks.includes(key);
  // Пустые стадии не рисуются карточками: одна строка «Пусто: …», непустые — как были.
  const showFilled = (key: string) => showBlock(key) && blockCount[key] > 0;
  const emptyTitles = BLOCKS.filter((block) => showBlock(block.key) && blockCount[block.key] === 0).map((block) => block.title);
  const toggleBlock = (key: string, shown: boolean) => setHiddenBlocks((current) => {
    const next = shown ? current.filter((item) => item !== key) : [...current.filter((item) => item !== key), key];
    writeSaved(BLOCKS_KEY, { hidden: next });
    return next;
  });

  const run = (line: ProcurementLine, operation: () => Promise<unknown>, message: string) => {
    void advance(line, operation, message).catch(() => undefined);
  };
  const cancelLine = (line: ProcurementLine) => {
    if (!window.confirm(`Отменить ${line.legacy_id}? Отмена окончательная.`)) return;
    run(line, () => procurementApi.cancelLine(line.id, line.version), `${line.legacy_id}: строка отменена`);
  };

  // Действия строки видны на ней (без меню «⋯»); формы раскрываются под таблицей стадии.
  const form = (line: ProcurementLine) => ({ line, busy: busyId === line.id, disabled: interactionLocked, onAdvance: advance });
  const traceability = (line: ProcurementLine): RowAction => ({ label: "Прослеживаемость…", short: "Прослеж.…", panel: () => <TraceabilityForm {...form(line)} /> });
  // Даташит, файлы разрешения и документа качества строки.
  const documents = (line: ProcurementLine): RowAction => ({
    label: "Документы…", short: "Документы…", title: "Даташит, файл разрешения, документ качества",
    panel: () => <LineDocumentsForm line={line} editing={editing} disabled={interactionLocked} onMessage={say} />,
  });
  const split = (line: ProcurementLine): RowAction => ({ label: "Раздробить по поставщикам…", short: "Раздробить…", panel: () => <SplitAction {...form(line)} /> });
  const cancel = (line: ProcurementLine): RowAction => ({ label: "Отменить строку", run: () => cancelLine(line) });
  const replaceInvoice = (line: ProcurementLine): RowAction[] => (
    line.invoice_url ? [{ label: "Заменить счёт…", short: "Заменить…", panel: () => <InvoiceReplaceForm {...form(line)} /> }] : []
  );

  const awaitingActions = (line: ProcurementLine): RowAction[] => [
    {
      label: "Суммы и счёт строки…",
      short: "Суммы…",
      panel: () => (
        <>
          <MoneyForm {...form(line)} />
          <MoneySummary line={line} />
          <RecordInvoiceForm {...form(line)} />
        </>
      ),
    },
    traceability(line), documents(line), split(line), cancel(line),
  ];
  const toPayActions = (line: ProcurementLine): RowAction[] => [
    {
      label: "Суммы по счёту…",
      short: "Суммы…",
      panel: () => line.pay_missing.length > 0
        ? <><MoneyForm {...form(line)} /><small className="hint">Оплата закрыта, пока не заполнено: {line.pay_missing.join(", ")}</small></>
        : <MoneySummary line={line} />,
    },
    {
      label: "Оплатить одну строку…",
      short: "Оплатить…",
      title: "Если ЦБ не отдал курс, его можно ввести вручную",
      panel: () => <PaymentAction {...form(line)} disabled={interactionLocked || line.pay_missing.length > 0} />,
    },
    ...replaceInvoice(line), traceability(line), documents(line), split(line), cancel(line),
  ];
  const backorderActions = (line: ProcurementLine): RowAction[] => [
    {
      label: "Отменить дозавоз",
      short: "Отменить",
      title: "Поставщик вернул деньги — остаток уйдёт новой заявкой",
      run: () => run(line, () => procurementApi.cancelBackorder(line.id, line.version), `${line.legacy_id}: дозавоз отменён, остаток ушёл новой заявкой`),
    },
    ...replaceInvoice(line), split(line),
  ];
  // «Готово · без ГРН» (закрыта «Есть на складе, без ГРН»): партию выбирают потом — резерв встаёт на неё целиком.
  const bindGrn = (line: ProcurementLine): RowAction[] => waitsForGrn(line) ? [{
    label: "Привязать ГРН",
    short: "ГРН…",
    title: "Закрыто «Есть на складе, без ГРН» — выберите партию, всё количество зарезервируется на ней",
    panel: () => (
      <ProcurementStockPicker
        line={line} bindOnly disabled={interactionLocked}
        onDone={(message) => { if (message) say(message); void reload(true); }}
      />
    ),
  }] : [];
  const paidActions = (line: ProcurementLine): RowAction[] => [
    ...bindGrn(line),
    ...(line.exchange_rate_manual ? [{
      label: "Сверить курс с ЦБ",
      short: "Курс с ЦБ",
      title: "Курс введён вручную — сверьте с ЦБ, когда он ответит",
      run: () => run(line, () => procurementApi.reconcileRate(line.id, line.version), `${line.legacy_id}: курс сверен с ЦБ`),
    }] : []),
    ...replaceInvoice(line), documents(line), split(line),
  ];
  const orderActions = (line: ProcurementLine): RowAction[] => (
    line.status === "Жду счет" ? awaitingActions(line) : line.status === "В оплату" ? toPayActions(line) : paidActions(line)
  );
  // Строка, поставленная на согласование до отключения этапа (владелец, 04.10), продолжает закупку отсюда.
  const otherActions = (line: ProcurementLine): RowAction[] => line.status === APPROVAL
    ? [
      { label: "Продолжить закупку", short: "Продолжить", run: () => run(line, () => procurementApi.resumeFromApproval(line.id, line.version), `${line.legacy_id}: строка вернулась в работу`) },
      cancel(line), split(line),
    ]
    : [split(line)];
  // Комментарий и ожидаемая дата правятся в ячейке: свежая строка встаёт на место без перезагрузки очереди,
  // а 409 перечитывает очередь (другая правка успела раньше).
  const editing: LineEditing = {
    // Слияние, а не замена: ответ правки ячейки не несёт полей «Моих позиций» (сколько ждёт «Привязать ГРН»).
    onSaved: (saved) => setMine((current) => current.map((item) => (item.id === saved.id ? { ...item, ...saved } : item))),
    onConflict: () => reload(true),
  };
  const openKp = (references: string[]) => {
    setKpQueue(references);
    setKpTotal(references.length);
  };
  const bulkOf = (block: BulkBlock) => ({ bulk: bulk?.block === block ? bulk : null, onCloseBulk: () => setBulk(null) });
  const stageProps = { disabled: interactionLocked, busyId, editing };
  const dense = useStageOption("procurement.dense");

  return (
    <section className={dense ? "page proc-dense" : "page"}>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}
      {warnings.length > 0 && (
        <div className="warning-banner" role="alert">
          <strong>Есть предупреждения</strong>
          <ul>{warnings.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      )}
      {skippedRequests.length > 0 && (
        <div className="warning-inline" role="alert">
          <strong>В запросы не попало строк: {skippedRequests.length}</strong>
          <ul>{skippedRequests.map((item) => <li key={item.text}>{item.text}</li>)}</ul>
          <button type="button" className="link-button" onClick={() => setSkippedRequests([])}>Закрыть</button>
        </div>
      )}

      <div className="queue-summary">
        {(readOnly || available.length > 0) && <span><strong>{available.length}</strong> доступно</span>}
        <span><strong>{mine.length}</strong> {readOnly ? "в работе" : "у меня"}</span>
        <button className="secondary small page-actions" onClick={() => void reload()} disabled={interactionLocked}>
          {refreshing ? "Обновляем…" : "Обновить"}
        </button>
      </div>

      {(readOnly || available.length > 0 || Boolean(availableCursor)) && <section className="panel table-panel procurement-stage">
        <StageHeading title="Неразобранные" count={available.length} hint={readOnly ? undefined : "Берите только то, что будете вести"} />
        <DataTable
          label="Неразобранные"
          rows={available}
          columns={readOnly ? availableColumns : [...availableColumns, {
            key: "take", title: "", value: () => "",
            render: (line) => <button className="secondary small" disabled={interactionLocked} onClick={() => void take(line)}>{busyId === line.id ? "Берём…" : "Взять"}</button>,
          }]}
          rowKey={(line) => line.id}
          storageKey="procurement-available"
          compactBelow={COMPACT_BELOW}
          emptyText="Свободных позиций нет"
          bulkActions={readOnly ? [] : [{ label: "Взять в работу", run: takeMany, disabled: () => interactionLocked }]}
        />
        {availableCursor && (
          <button className="secondary" disabled={interactionLocked} onClick={() => void loadMore("available", availableCursor)}>
            {loadingMore === "available" ? "Загружаем…" : "Ещё неразобранные"}
          </button>
        )}
      </section>}

      {readOnly && (
        <section className="panel table-panel procurement-stage">
          <div className="panel-heading"><h2>В работе у закупщиков</h2><span>Только просмотр</span></div>
          <DataTable
            label="В работе у закупщиков"
            rows={mine}
            columns={inWorkColumns}
            rowKey={(line) => line.id}
            storageKey="procurement-in-work"
            emptyText="В работе ничего нет"
          />
          {mineCursor && (
            <button className="secondary" disabled={interactionLocked} onClick={() => void loadMore("mine", mineCursor)}>
              {loadingMore === "mine" ? "Загружаем…" : "Ещё в работе"}
            </button>
          )}
        </section>
      )}

      {!readOnly && <section className="work-list">
        <h2 className="section-title">Мои позиции</h2>
        {!loaded ? (refreshing ? <p className="empty">Загрузка…</p> : null) : <>
        <div className="blocks-bar">
          <input
            type="search" className="blocks-search" aria-label="Поиск по моим позициям" placeholder="Поиск: ID, наименование, поставщик, № заказа, № счёта"
            value={sectionQuery} onChange={(event) => setSectionQuery(event.target.value)}
          />
          <button type="button" className="secondary small" aria-expanded={choosingBlocks} onClick={() => setChoosingBlocks((current) => !current)}>Блоки</button>
          {hiddenWithData.length > 0 && (
            <span className="blocks-hidden">скрыто: {hiddenWithData.map((block) => `${block.title} ${blockCount[block.key]}`).join(", ")}</span>
          )}
        </div>
        {choosingBlocks && (
          <fieldset className="column-chooser stage-columns">
            <legend>Показывать блоки</legend>
            {BLOCKS.map((block) => (
              <label key={block.key} className="check-inline">
                <input type="checkbox" checked={showBlock(block.key)} onChange={(event) => toggleBlock(block.key, event.target.checked)} />
                {block.title}
              </label>
            ))}
          </fieldset>
        )}
        {!mine.length && !needle && (
          <div className="procurement-empty">
            <span>{readOnly ? "Сейчас никто не ведёт позиций." : "У вас нет позиций в работе. Возьмите позицию из «Неразобранных» или посмотрите, что на листе."}</span>
            {onOpenSheet && <button type="button" className="secondary small" onClick={onOpenSheet}>Открыть весь лист</button>}
          </div>
        )}
        {emptyTitles.length > 0 && <p className="empty-stages">Пусто: {emptyTitles.join(", ")}</p>}
        {showFilled("inwork") && <ProcurementInWorkTable
          lines={inWorkBlock}
          units={units}
          disabled={interactionLocked}
          busyId={busyId}
          editing={editing}
          filling={autofill}
          {...bulkOf("inwork")}
          onChanged={(message) => { say(message); void reload(true); }}
          onError={setError}
          onCancel={cancelLine}
          onCancelMany={cancelMany}
          onBuildRequests={buildRequests}
          renderDetail={(line) => (
            <>
              <TraceabilityForm {...form(line)} />
              <LineDocumentsForm line={line} editing={editing} disabled={interactionLocked} onMessage={say} />
              <SplitAction {...form(line)} />
            </>
          )}
        />}
        {ORDER_BLOCKS.map(({ stage, title, hint, initiallyCollapsed }) => {
          const groups = ordersAt(stage);
          return showFilled(stage) && (
            <OrderStageBlock
              key={stage} stage={stage}
              {...stageProps} {...(stage === "transit" ? bulkOf("orders") : {})}
              title={title} hint={hint} block={`orders-${stage}`} initiallyCollapsed={initiallyCollapsed}
              lines={groups.flatMap((group) => group.lines)}
              groups={groups}
              actionsOf={orderActions}
              onOpenKp={openKp}
              onPaidOrder={paidOrder}
              onReconcile={reconcileMany}
              onRecognize={recognizeDocument}
              onApplyDocument={applyDocument}
            />
          );
        })}
        {kpReference && (
          <KpForm
            key={kpReference}
            reference={kpReference}
            queue={kpTotal > 1 ? { index: kpTotal - kpQueue.length + 1, total: kpTotal } : undefined}
            onClose={() => setKpQueue([])}
            onSaved={(message, failure, warns) => {
              say(message, warns);
              setError(failure);
              setKpQueue((current) => current.slice(1));
              void reload(true);
            }}
          />
        )}
        {showFilled("backorder") && (
          <BackorderStage
            {...stageProps}
            lines={backorders}
            actionsOf={backorderActions}
            onConfirmBackorder={(line, date) => run(line, () => procurementApi.confirmBackorder(line.id, line.version, date), `${line.legacy_id}: досыл подтверждён, передано в приёмку`)}
          />
        )}
        {showFilled("other") && <OtherStage {...stageProps} lines={other} actionsOf={otherActions} />}
        {mineCursor && (
          <button className="secondary" disabled={interactionLocked} onClick={() => void loadMore("mine", mineCursor)}>
            {loadingMore === "mine" ? "Загружаем…" : "Ещё мои"}
          </button>
        )}
        </>}
      </section>}
    </section>
  );
}

/** «Жду счет», счёт строки по одному (когда он не общий на заказ): файл → ссылка, № и дата → «В оплату». */
function RecordInvoiceForm({ line, busy, disabled, onAdvance }: {
  line: ProcurementLine;
  busy: boolean;
  disabled: boolean;
  onAdvance: Advance;
}) {
  const [invoiceUrl, setInvoiceUrl] = useState(line.invoice_url ?? "");
  // № and date of an uploaded file; empty keeps what the money form saved (uploadInvoice setIf).
  const [invoiceFile, setInvoiceFile] = useState(false);
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [invoiceDate, setInvoiceDate] = useState("");
  const [expectedArrival, setExpectedArrival] = useState(line.expected_arrival ?? "");
  const moneyMissing = line.pay_missing.filter((name) => name !== "ОЖИД. ДАТА");

  return (
    <>
      <form className="inline-action" onSubmit={(event) => { event.preventDefault(); void onAdvance(line, () => procurementApi.recordInvoice(line.id, line.version, invoiceUrl, expectedArrival, invoiceNumber, invoiceDate), `${line.legacy_id}: счёт передан в оплату`).catch(() => undefined); }}>
        <InvoiceFileUpload lineId={line.id} disabled={disabled} onUploaded={(upload) => {
          setInvoiceUrl(upload.url);
          setInvoiceFile(true);
          setInvoiceNumber(upload.invoice_number);
          setInvoiceDate(upload.invoice_date);
        }} />
        <label>Ссылка на счёт<input required value={invoiceUrl} onChange={(event) => setInvoiceUrl(event.target.value)} /></label>
        {invoiceFile && <>
          <label>№ счёта по файлу<input value={invoiceNumber} onChange={(event) => setInvoiceNumber(event.target.value)} placeholder={line.invoice_number ?? ""} /></label>
          <label>Дата счёта по файлу<input type="date" value={invoiceDate} onChange={(event) => setInvoiceDate(event.target.value)} /></label>
        </>}
        <label>Ожидаемая дата<input type="date" required value={expectedArrival} onChange={(event) => setExpectedArrival(event.target.value)} /></label>
        <button className="primary small" disabled={disabled || moneyMissing.length > 0}>{busy ? "Сохраняем…" : "В оплату"}</button>
      </form>
      {moneyMissing.length > 0 && <small className="hint">Для счёта не хватает: {moneyMissing.join(", ")}</small>}
    </>
  );
}

// Invoices.gs uploadInvoice on a line that already has one: a correction — payment and status stay.
function InvoiceReplaceForm({ line, busy, disabled, onAdvance }: {
  line: ProcurementLine;
  busy: boolean;
  disabled: boolean;
  onAdvance: Advance;
}) {
  const [url, setUrl] = useState("");
  const [number, setNumber] = useState("");
  const [date, setDate] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    void onAdvance(line, () => procurementApi.replaceInvoice(line.id, line.version, url, number, date), `${line.legacy_id}: счёт заменён, оплата и статус не менялись`).catch(() => undefined);
  }

  return (
    <details>
      <summary>Заменить счёт</summary>
      <form className="inline-action" onSubmit={submit}>
        <InvoiceFileUpload lineId={line.id} disabled={disabled} onUploaded={(upload) => {
          setUrl(upload.url);
          if (upload.invoice_number) setNumber(upload.invoice_number);
          if (upload.invoice_date) setDate(upload.invoice_date);
        }} />
        <label>Ссылка на новый счёт<input required value={url} onChange={(event) => setUrl(event.target.value)} /></label>
        <label>№ счёта<input value={number} onChange={(event) => setNumber(event.target.value)} placeholder={line.invoice_number ?? ""} /></label>
        <label>Дата счёта<input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
        <button className="secondary small" disabled={disabled}>{busy ? "Сохраняем…" : "Заменить"}</button>
      </form>
    </details>
  );
}

// Invoices.gs uploadInvoice: the file goes to ERP storage and its link, № and date fill the form —
// the buyer checks them and sends the form (SUPPLYMAP wrote them into the rows at once).
function InvoiceFileUpload({ lineId, disabled, onUploaded }: {
  lineId: string;
  disabled: boolean;
  onUploaded: (upload: InvoiceUpload) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  async function upload(file: File) {
    setBusy(true);
    setNote("");
    setError("");
    try {
      const result = await procurementApi.uploadInvoiceFile(lineId, {
        name: file.name, type: file.type || "application/octet-stream", base64: await readBase64(file),
      });
      onUploaded(result);
      setNote(result.hint
        ? `${result.file_name} · ${result.hint}`
        : `${result.file_name} · распознано: № ${result.invoice_number || "—"}, дата ${result.invoice_date || "—"} — проверьте`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Счёт не загружен");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <label>Файл счёта
        <input type="file" accept=".pdf,image/*" disabled={disabled || busy} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file); }} />
      </label>
      {busy && <small className="hint">Загружаем счёт…</small>}
      {note && <small className="hint">{note}</small>}
      {error && <small className="danger-text" role="alert">{error}</small>}
    </>
  );
}

// «Раздробить по поставщикам» (Pipeline.gs:127-220): N копий с новыми System ID уходят в «Новые».
function SplitAction({ line, busy, disabled, onAdvance }: {
  line: ProcurementLine;
  busy: boolean;
  disabled: boolean;
  onAdvance: Advance;
}) {
  const [copies, setCopies] = useState("1");

  function split(event: FormEvent) {
    event.preventDefault();
    const count = Number(copies);
    void onAdvance(line, () => procurementApi.split(line.id, line.version, count), `${line.legacy_id}: создано копий — ${count}, они в очереди «Новые»`).catch(() => undefined);
  }

  return (
    <form className="inline-action" onSubmit={split}>
      <label>Раздробить по поставщикам, копий<input type="number" min="1" max="30" step="1" required value={copies} onChange={(event) => setCopies(event.target.value)} /></label>
      <button className="secondary small" disabled={disabled}>{busy ? "Дробим…" : "Раздробить"}</button>
    </form>
  );
}

// «Лист закупки» TRACE_COLUMNS.PURCHASE: ИЗГОТОВИТЕЛЬ, СТРАНА, СТАНДАРТ — уходят в приёмку со снимком поставки;
// пустая СТРАНА подставляется по изготовителю из справочника. Тип, № и дата документа качества на экране не
// показываются (владелец снял блок 29.08, Portal.gs PORTAL_RETIRED_COLS_), но уже записанные значения уходят
// в сохранение как есть — данные не теряются.
function TraceabilityForm({ line, busy, disabled, onAdvance }: {
  line: ProcurementLine;
  busy: boolean;
  disabled: boolean;
  onAdvance: Advance;
}) {
  const initial = (): ProcurementTraceabilityInput => ({
    manufacturer: line.manufacturer ?? "",
    country: line.country ?? "",
    quality_document_type: line.quality_document_type ?? "",
    quality_document_number: line.quality_document_number ?? "",
    quality_document_date: line.quality_document_date ?? null,
    standard: line.standard ?? "",
  });
  const [draft, setDraft] = useState<ProcurementTraceabilityInput>(initial);
  useEffect(() => { setDraft(initial()); }, [line.version]);

  function field(name: "manufacturer" | "country" | "standard", value: string) {
    setDraft((current) => ({ ...current, [name]: value }));
  }

  function save(event: FormEvent) {
    event.preventDefault();
    void onAdvance(line, () => procurementApi.setTraceability(line.id, line.version, draft), `${line.legacy_id}: прослеживаемость сохранена`).catch(() => undefined);
  }

  return (
    <form onSubmit={save}>
      <div className="form-grid">
        <label>Изготовитель<input value={draft.manufacturer} onChange={(e) => field("manufacturer", e.target.value)} /></label>
        <label>Страна<input value={draft.country} onChange={(e) => field("country", e.target.value)} placeholder="по изготовителю" /></label>
        <label>Стандарт<input value={draft.standard} onChange={(e) => field("standard", e.target.value)} /></label>
      </div>
      <div className="card-footer"><small>Уходит в приёмку при оплате</small><button className="secondary small" disabled={disabled || busy}>Сохранить прослеживаемость</button></div>
    </form>
  );
}

type Advance = (line: ProcurementLine, operation: () => Promise<unknown>, message: string) => Promise<void>;

type MoneyDraft = {
  currency: string;
  price: string;
  vat_rate: string;
  price_with_vat: string;
  sum: string;
  invoice_item_name: string;
  invoice_number: string;
  invoice_date: string;
};

function moneyDraftFrom(line: ProcurementLine): MoneyDraft {
  // A converted line is edited in its invoice currency: the typed amounts, not the rouble result.
  const source = Boolean(line.source_currency);
  return {
    currency: line.source_currency ?? line.currency ?? "RUB",
    price: (source ? line.source_price : line.actual_price) ?? "",
    vat_rate: line.actual_vat_rate ?? "22",
    price_with_vat: (source ? line.source_price_with_vat : line.actual_price_with_vat) ?? "",
    sum: (source ? line.source_sum : line.actual_sum) ?? "",
    invoice_item_name: line.invoice_item_name ?? line.calculated_name,
    invoice_number: line.invoice_number ?? "",
    invoice_date: line.invoice_date ?? "",
  };
}

const DRIVER_BY_FIELD: Partial<Record<keyof MoneyDraft, MoneyDriver>> = {
  price: "net",
  vat_rate: "vat",
  price_with_vat: "gross",
  sum: "sum",
};

function MoneyForm({ line, busy, disabled, onAdvance }: {
  line: ProcurementLine;
  busy: boolean;
  disabled: boolean;
  onAdvance: Advance;
}) {
  const [draft, setDraft] = useState(() => moneyDraftFrom(line));
  const [driver, setDriver] = useState<MoneyDriver | undefined>();
  const [prompt, setPrompt] = useState<ManualRatePrompt | null>(null);
  const [manualRate, setManualRate] = useState("");

  function field(name: keyof MoneyDraft, value: string) {
    setDraft((current) => ({ ...current, [name]: value }));
    const edited = DRIVER_BY_FIELD[name];
    if (edited) setDriver(edited);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const blank = (value: string) => value.trim() || null;
    const money = {
      driver,
      currency: draft.currency.trim(),
      price: blank(draft.price),
      vat_rate: blank(draft.vat_rate),
      price_with_vat: blank(draft.price_with_vat),
      sum: blank(draft.sum),
      invoice_item_name: blank(draft.invoice_item_name),
      invoice_number: blank(draft.invoice_number),
      invoice_date: blank(draft.invoice_date),
      manual_exchange_rate: prompt ? blank(manualRate) : null,
    };
    void onAdvance(line, () => procurementApi.saveMoney(line.id, line.version, money), `${line.legacy_id}: суммы по счёту сохранены`)
      .then(() => setPrompt(null))
      .catch((reason) => setPrompt(manualRatePrompt(reason)));
  }

  return (
    <form onSubmit={submit}>
      <div className="facts-grid">
        <label>Валюта *<input list="procurement-currencies" required maxLength={8} value={draft.currency} onChange={(e) => field("currency", e.target.value)} /></label>
        <label>Цена без НДС<input inputMode="decimal" value={draft.price} onChange={(e) => field("price", e.target.value)} /></label>
        <label>НДС, %<input inputMode="decimal" value={draft.vat_rate} onChange={(e) => field("vat_rate", e.target.value)} /></label>
        <label>Цена с НДС<input inputMode="decimal" value={draft.price_with_vat} onChange={(e) => field("price_with_vat", e.target.value)} /></label>
        <label>Сумма<input inputMode="decimal" value={draft.sum} onChange={(e) => field("sum", e.target.value)} /></label>
        <label>Наименование по счёту *<input required value={draft.invoice_item_name} onChange={(e) => field("invoice_item_name", e.target.value)} /></label>
        <label>№ счёта<input value={draft.invoice_number} onChange={(e) => field("invoice_number", e.target.value)} /></label>
        <label>Дата счёта<input type="date" value={draft.invoice_date} onChange={(e) => field("invoice_date", e.target.value)} /></label>
        {prompt && (
          <label className="wide">{prompt.message}. Курс, ₽ за 1 {prompt.currency} *
            <input inputMode="decimal" required value={manualRate} onChange={(e) => setManualRate(e.target.value)} />
          </label>
        )}
      </div>
      <datalist id="procurement-currencies">
        <option value="RUB" /><option value="USD" /><option value="EUR" /><option value="CNY" />
      </datalist>
      <div className="card-footer">
        <small>Инвалюта пересчитывается в рубли по курсу ЦБ + 6,5 %</small>
        <button className="primary small" disabled={disabled}>{busy ? "Сохраняем…" : "Сохранить суммы"}</button>
      </div>
    </form>
  );
}

function MoneySummary({ line }: { line: ProcurementLine }) {
  if (!line.actual_sum) return null;
  return (
    <p className="money-summary">
      <small>
        Сумма {line.actual_sum} ₽ · цена {line.actual_price ?? "—"} ₽ без НДС / {line.actual_price_with_vat ?? "—"} ₽ с НДС
        {line.actual_vat_rate ? ` (НДС ${line.actual_vat_rate} %)` : ""}
        {line.invoice_number ? ` · счёт № ${line.invoice_number}` : ""}
        {line.source_currency && (line.source_sum
          ? ` · в счёте ${line.source_sum} ${line.source_currency}`
          : ` · в счёте цена ${line.source_price_with_vat ?? line.source_price} ${line.source_currency}`)}
        {line.source_currency && ` по курсу ЦБ ${line.exchange_rate} на ${line.exchange_rate_date}`}
        {line.exchange_rate_manual && " · курс введён вручную"}
      </small>
    </p>
  );
}

function PaymentAction({ line, busy, disabled, onAdvance }: {
  line: ProcurementLine;
  busy: boolean;
  disabled: boolean;
  onAdvance: Advance;
}) {
  const [prompt, setPrompt] = useState<ManualRatePrompt | null>(null);
  const [manualRate, setManualRate] = useState("");

  function pay(event: FormEvent) {
    event.preventDefault();
    void onAdvance(line, () => procurementApi.markPaid(line.id, line.version, prompt ? manualRate.trim() : undefined), `${line.legacy_id}: оплачено, передано в приёмку`)
      .catch((reason) => setPrompt(manualRatePrompt(reason)));
  }

  return (
    <form className="card-footer" onSubmit={pay}>
      <small>{line.invoice_url} · ожидается {line.expected_arrival}</small>
      {prompt && (
        <label>{prompt.message}. Курс, ₽ за 1 {prompt.currency} *
          <input inputMode="decimal" required value={manualRate} onChange={(e) => setManualRate(e.target.value)} />
        </label>
      )}
      <button className="primary small" disabled={disabled}>{busy ? "Проводим…" : "Оплачено → в пути"}</button>
    </form>
  );
}

type KpDraft = Omit<KpRowInput, "line_id" | "version" | "driver"> & { driver?: MoneyDriver };
type KpField = keyof Omit<KpDraft, "driver">;

function kpDraftFrom(line: ProcurementLine): Record<KpField, string> {
  const money = moneyDraftFrom(line);
  return {
    actual_quantity: line.actual_quantity ?? line.calculated_quantity,
    actual_unit: line.actual_unit ?? line.calculated_unit,
    currency: money.currency,
    price: money.price,
    vat_rate: money.vat_rate,
    price_with_vat: money.price_with_vat,
    sum: money.sum,
    invoice_item_name: line.invoice_item_name ?? "",
    invoice_number: money.invoice_number,
    invoice_date: money.invoice_date,
    expected_arrival: line.expected_arrival ?? "",
  };
}

const KP_COLUMNS: { field: KpField; label: string; type?: string; fillAll?: boolean }[] = [
  { field: "actual_quantity", label: "Кол-во (факт)" },
  { field: "actual_unit", label: "Ед. (факт)" },
  { field: "price", label: "Цена" },
  { field: "vat_rate", label: "НДС, %", fillAll: true },
  { field: "price_with_vat", label: "Цена с НДС" },
  { field: "sum", label: "Сумма" },
  { field: "currency", label: "Валюта", fillAll: true },
  { field: "expected_arrival", label: "Ожид. дата", type: "date", fillAll: true },
  { field: "invoice_item_name", label: "Наименование (счёт)" },
  { field: "invoice_number", label: "№ счёта", fillAll: true },
  { field: "invoice_date", label: "Дата счёта", type: "date", fillAll: true },
];

/** SUPPLYMAP «Внести КП» (Kp.gs, KpForm.html): every «Жду счет» line of one supplier order at once. */
function KpForm({ reference, queue, onClose, onSaved }: {
  reference: string;
  /** Несколько выбранных заказов идут по очереди: этот — `index` из `total`. */
  queue?: { index: number; total: number };
  onClose: () => void;
  onSaved: (message: string, failure: string, warnings: string[]) => void;
}) {
  const [lines, setLines] = useState<ProcurementLine[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Record<KpField, string>>>({});
  const [drivers, setDrivers] = useState<Record<string, MoneyDriver | undefined>>({});
  const [invoiceUrl, setInvoiceUrl] = useState("");
  const [message, setMessage] = useState("");
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const [prompt, setPrompt] = useState<ManualRatePrompt | null>(null);
  const [manualRate, setManualRate] = useState("");

  useEffect(() => {
    procurementApi.kpOrder(reference)
      .then((result) => {
        setLines(result.lines);
        setDrafts(Object.fromEntries(result.lines.map((line) => [line.id, kpDraftFrom(line)])));
      })
      .catch((reason) => setFailure(reason instanceof Error ? reason.message : "Заказ не загружен"));
  }, [reference]);

  function field(lineId: string, name: KpField, value: string) {
    setDrafts((current) => ({ ...current, [lineId]: { ...current[lineId], [name]: value } }));
    const edited = DRIVER_BY_FIELD[name as keyof MoneyDraft];
    if (edited) setDrivers((current) => ({ ...current, [lineId]: edited }));
  }

  function fillAll(name: KpField, value: string) {
    setDrafts((current) => Object.fromEntries(
      Object.entries(current).map(([id, draft]) => [id, { ...draft, [name]: value }]),
    ));
  }

  async function importFile(file: File) {
    setBusy(true);
    setFailure("");
    try {
      const result = await procurementApi.parseKp(reference, file.name, await readBase64(file));
      setDrafts((current) => {
        const next = { ...current };
        for (const match of result.matches) {
          const draft = { ...next[match.line_id] };
          if (match.name) draft.invoice_item_name = match.name;
          if (match.qty) draft.actual_quantity = match.qty;
          if (match.price) draft.price = match.price;
          if (match.vat) draft.vat_rate = match.vat;
          next[match.line_id] = draft;
        }
        return next;
      });
      setDrivers((current) => ({
        ...current,
        ...Object.fromEntries(result.matches.filter((match) => match.price).map((match) => [match.line_id, "net" as MoneyDriver])),
      }));
      const summary = `Подтянуто строк: ${result.matches.length} из ${result.items.length}`;
      if (result.not_found.length) setFailure(`${summary} · не найдены в заказе: ${result.not_found.join("; ")}`);
      else setMessage(summary);
    } catch (reason) {
      setFailure(reason instanceof Error ? `Ошибка импорта: ${reason.message}` : "Ошибка импорта");
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFailure("");
    const blank = (value: string) => value.trim() || null;
    const rows: KpRowInput[] = lines.map((line) => {
      const draft = drafts[line.id];
      return {
        line_id: line.id,
        version: line.version,
        driver: drivers[line.id],
        ...(Object.fromEntries(KP_COLUMNS.map(({ field: name }) => [name, blank(draft[name])])) as Omit<KpRowInput, "line_id" | "version" | "driver">),
      };
    });
    try {
      const result = await procurementApi.saveKp(reference, rows, invoiceUrl.trim(), prompt ? manualRate.trim() : undefined);
      const saved = `Заказ ${reference}: КП сохранено по ${result.lines.length} строкам`;
      onSaved(
        result.invoice_error ? saved : invoiceUrl.trim() ? `${saved}, счёт передан в оплату` : saved,
        result.invoice_error ? `Счёт не принят, данные КП сохранены: ${result.invoice_error}` : "",
        result.warnings ?? [],
      );
    } catch (reason) {
      const ratePrompt = manualRatePrompt(reason);
      setPrompt(ratePrompt);
      setFailure(ratePrompt ? "" : reason instanceof Error ? reason.message : "КП не сохранено");
      setBusy(false);
    }
  }

  return (
    <form className="panel table-panel" onSubmit={submit} aria-label={`КП по заказу ${reference}`}>
      <div className="panel-heading">
        <h2>Внести КП · заказ {reference}{queue && <span className="stage-count">{queue.index} из {queue.total}</span>}</h2>
        <span>Строки заказа в статусе «Жду счет»</span>
      </div>
      {failure && <div className="error-banner" role="alert">{failure}</div>}
      {message && <div className="success-banner">{message}</div>}
      <div className="table-scroll">
        <table>
          <thead>
            <tr><th>Позиция</th>{KP_COLUMNS.map((column) => <th key={column.field}>{column.label}</th>)}</tr>
            <tr>
              <th><small>всем</small></th>
              {KP_COLUMNS.map((column) => (
                <th key={column.field}>
                  {column.fillAll && <input aria-label={`${column.label} — всем`} type={column.type} onChange={(e) => fillAll(column.field, e.target.value)} />}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.id}>
                <td><strong>{line.legacy_id}</strong><small>{line.calculated_name}{line.site_name ? ` · ${line.site_name}` : ""} · {line.calculated_quantity} {line.calculated_unit}</small></td>
                {KP_COLUMNS.map((column) => (
                  <td key={column.field}>
                    <input
                      aria-label={`${line.legacy_id} ${column.label}`}
                      type={column.type}
                      inputMode={column.type ? undefined : "decimal"}
                      value={drafts[line.id]?.[column.field] ?? ""}
                      onChange={(e) => field(line.id, column.field, e.target.value)}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="inline-action">
        <label>Файл КП (.xlsx, .csv)
          <input type="file" accept=".xlsx,.csv" disabled={busy || !lines.length} onChange={(e) => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void importFile(file); }} />
        </label>
        {lines.length > 0 && <InvoiceFileUpload lineId={lines[0].id} disabled={busy} onUploaded={(upload) => {
          setInvoiceUrl(upload.url);
          if (upload.invoice_number) fillAll("invoice_number", upload.invoice_number);
          if (upload.invoice_date) fillAll("invoice_date", upload.invoice_date);
        }} />}
        <label>Ссылка на счёт (один на заказ, необязательно)<input value={invoiceUrl} onChange={(e) => setInvoiceUrl(e.target.value)} /></label>
        {prompt && (
          <label>{prompt.message}. Курс, ₽ за 1 {prompt.currency} *
            <input inputMode="decimal" required value={manualRate} onChange={(e) => setManualRate(e.target.value)} />
          </label>
        )}
      </div>
      <div className="card-footer">
        <small>Без счёта статус не меняется; со счётом все строки уходят «В оплату»</small>
        <button type="button" className="secondary small" onClick={onClose} disabled={busy}>Закрыть</button>
        <button className="primary small" disabled={busy || !lines.length}>{busy ? "Сохраняем…" : "Сохранить КП"}</button>
      </div>
    </form>
  );
}
