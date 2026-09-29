import { useEffect, useState, type FormEvent } from "react";
import {
  manualRatePrompt,
  procurementApi,
  type InvoiceUpload,
  type KpRowInput,
  type ManualRatePrompt,
  type MoneyDriver,
  type ProcurementLine,
  type ProcurementTraceabilityInput,
  type SupplierOrder,
} from "./api";
import { openPrintSheet } from "./print";
import { DataTable, type DataColumn } from "./DataTable";
import { ProcurementBySupplier, ProcurementSheetView } from "./ProcurementSheet";
import { ProcurementFillForm } from "./ProcurementFillForm";

// ЕД.ИЗМ (факт) — выпадающий список, как в SUPPLYMAP (фолбэк REQUEST_FORM_FIELDS['ЕД.ИЗМ']); сервер отдаёт его в /meta.
const DEFAULT_UNITS = ["шт", "кг", "м", "л", "уп", "компл"];

type ProcurementTab = "queues" | "sheet" | "suppliers";

const availableColumns: DataColumn<ProcurementLine>[] = [
  { key: "legacy_id", title: "ID", value: (line) => line.legacy_id, render: (line) => <><strong>{line.legacy_id}</strong><small>рев. {line.revision}</small></> },
  { key: "name", title: "Позиция", value: (line) => line.calculated_name, render: (line) => <>{line.calculated_name}<small>{line.designation}</small></> },
  { key: "project", title: "Проект", value: (line) => line.project_reference, render: (line) => <>{line.project_reference || "—"}<small>{line.delivery_point}</small></> },
  { key: "planned", title: "Срок", value: (line) => line.planned_date, render: (line) => <>{line.planned_date}<small>{line.priority}</small></> },
  { key: "priority", title: "Приоритет", value: (line) => line.priority, filter: true, hidden: true },
  { key: "qty", title: "Кол-во", value: (line) => `${line.calculated_quantity} ${line.calculated_unit}` },
];

const inWorkColumns: DataColumn<ProcurementLine>[] = [
  { key: "legacy_id", title: "ID", value: (line) => line.legacy_id, render: (line) => <><strong>{line.legacy_id}</strong><small>{line.project_reference || line.designation}</small></> },
  { key: "name", title: "Позиция", value: (line) => line.calculated_name, render: (line) => <>{line.calculated_name}<small>{line.actual_quantity ?? line.calculated_quantity} {line.actual_unit ?? line.calculated_unit}</small></> },
  { key: "buyer", title: "Закупщик", value: (line) => line.buyer },
  { key: "status", title: "Статус", value: (line) => line.status, filter: true },
  { key: "supplier", title: "Поставщик · заказ", value: (line) => line.supplier, render: (line) => <>{line.supplier || "—"}<small>{line.order_number || ""}</small></> },
  { key: "eta", title: "Ожидается", value: (line) => line.expected_arrival },
];

const toPayColumns: DataColumn<ProcurementLine>[] = [
  { key: "legacy_id", title: "ID", value: (line) => line.legacy_id },
  { key: "order", title: "№ заказа", value: (line) => line.order_number },
  { key: "supplier", title: "Поставщик", value: (line) => line.supplier },
  { key: "name", title: "Наименование", value: (line) => line.calculated_name },
  { key: "sum", title: "Сумма", value: (line) => line.actual_sum, numeric: true },
  { key: "invoice", title: "Счёт", value: (line) => line.invoice_url, render: (line) => line.invoice_url ? <a href={line.invoice_url} target="_blank" rel="noreferrer">счёт</a> : "—" },
];

/** `portalPurchaseToPay`: свои строки «В оплату» со счётом — кандидаты на «Оплачено → В пути». */
function payable(line: ProcurementLine) {
  return line.status === "В оплату" && Boolean(line.invoice_url) && !line.paid_at;
}

type FactsDraft = {
  supplier: string;
  site_name: string;
  link: string;
  design_article: string;
  calculated_name: string;
  actual_quantity: string;
  actual_unit: string;
};

function draftFrom(line: ProcurementLine): FactsDraft {
  return {
    supplier: line.supplier ?? "",
    site_name: line.site_name ?? "",
    link: line.link ?? "",
    design_article: line.design_article ?? "",
    calculated_name: line.calculated_name,
    actual_quantity: line.actual_quantity ?? line.calculated_quantity,
    actual_unit: line.actual_unit ?? line.calculated_unit,
  };
}

/** `readOnly` — «режим просмотра» (ID-6): free lines and every buyer's lines in work are read, nothing is taken
 *  or changed (SUPPLYMAP «Лист закупки — просмотр, что едет»: portalApplyAccess_ drops edit and canWork). */
export function ProcurementPage({ readOnly = false }: { readOnly?: boolean } = {}) {
  const [tab, setTab] = useState<ProcurementTab>("queues");
  return (
    <>
      <nav className="shipping-tabs" aria-label="Разделы закупки">
        <button type="button" className={`secondary small${tab === "queues" ? " active" : ""}`} aria-pressed={tab === "queues"} onClick={() => setTab("queues")}>Очереди</button>
        <button type="button" className={`secondary small${tab === "sheet" ? " active" : ""}`} aria-pressed={tab === "sheet"} onClick={() => setTab("sheet")}>Весь лист</button>
        <button type="button" className={`secondary small${tab === "suppliers" ? " active" : ""}`} aria-pressed={tab === "suppliers"} onClick={() => setTab("suppliers")}>По поставщикам</button>
      </nav>
      {tab === "queues" && <ProcurementQueues readOnly={readOnly} />}
      {tab === "sheet" && <section className="page"><ProcurementSheetView /></section>}
      {tab === "suppliers" && <section className="page"><ProcurementBySupplier /></section>}
    </>
  );
}

function ProcurementQueues({ readOnly }: { readOnly: boolean }) {
  const [units, setUnits] = useState<string[]>(DEFAULT_UNITS);
  const [filling, setFilling] = useState(false);
  const [available, setAvailable] = useState<ProcurementLine[]>([]);
  const [mine, setMine] = useState<ProcurementLine[]>([]);
  const [availableCursor, setAvailableCursor] = useState<string | null>(null);
  const [mineCursor, setMineCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyId, setBusyId] = useState("");
  const [orders, setOrders] = useState<SupplierOrder[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState<"available" | "mine" | "">("");
  const [kpReference, setKpReference] = useState("");
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
      setAvailable(availableResult.lines);
      setMine(mineResult.lines);
      setAvailableCursor(availableResult.next_cursor);
      setMineCursor(mineResult.next_cursor);
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
  }, []);

  /** `portalTakeInWork`: выбранные «Новый» строки — одним запросом. */
  async function takeMany(lines: ProcurementLine[]) {
    if (interactionLocked || !lines.length) return;
    setBusyId("take-many");
    setError("");
    try {
      const result = await procurementApi.take(lines.map((line) => line.id));
      const skipped = result.skipped ?? [];
      setNotice(`Взято в работу: ${result.lines.length}` + (skipped.length ? `; пропущено (уже в работе или дальше): ${skipped.join(", ")}` : ""));
      await reload(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось взять строки");
      throw reason;
    } finally {
      setBusyId("");
    }
  }

  /** `portalMarkPaid`: выбранные строки со счётом → «В пути»; уже оплаченные и без счёта пропускаются. */
  async function markPaidMany(lines: ProcurementLine[]) {
    if (interactionLocked || !lines.length) return;
    setBusyId("paid-many");
    setError("");
    try {
      const result = await procurementApi.markPaidBatch(lines.map((line) => ({ line_id: line.id, version: line.version })));
      const parts = [`Оплачено → В пути: ${result.done}`];
      if (result.already) parts.push(`уже оплачено: ${result.already}`);
      if (result.no_invoice.length) parts.push(`без счёта: ${result.no_invoice.join(", ")}`);
      setNotice(parts.join("; "));
      if (result.failed.length) setError(result.failed.map((item) => `${item.legacy_id}: ${item.error}`).join("; "));
      await reload(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Оплата не проведена");
      throw reason;
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
      setNotice(result.skipped?.length ? `${line.legacy_id} пропущена: уже в работе или дальше` : `${line.legacy_id} взята в работу`);
      await reload(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось взять строку");
    } finally {
      setBusyId("");
    }
  }

  async function save(line: ProcurementLine, draft: FactsDraft) {
    if (interactionLocked) return;
    setBusyId(line.id);
    setError("");
    try {
      const facts = Object.fromEntries(
        Object.entries(draft).map(([key, value]) => [key, value.trim() || null]),
      );
      await procurementApi.save(line.id, { version: line.version, ...facts });
      setNotice(`${line.legacy_id} сохранена`);
      await reload(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сохранить строку");
      throw reason;
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
      await operation();
      setNotice(message);
      await reload(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Переход закупки не выполнен");
      throw reason;
    } finally {
      setBusyId("");
    }
  }

  async function buildRequests() {
    if (interactionLocked) return;
    setBusyId("supplier-requests");
    setError("");
    try {
      const result = await procurementApi.buildSupplierRequests();
      setOrders(result.orders);
      const parts = [`Сформировано запросов: ${result.orders.length}`];
      if (result.skipped.length) parts.push(`пропущено: ${result.skipped.join(", ")}`);
      if (result.other_buyer) parts.push(`строк других закупщиков не тронуто: ${result.other_buyer}`);
      setNotice(parts.join("; "));
      await reload(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Запросы поставщикам не сформированы");
    } finally {
      setBusyId("");
    }
  }

  const readyCount = mine.filter((line) => line.status === "Готов к запросу").length;

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <span className="eyebrow">{readOnly ? "Просмотр" : "Рабочая очередь"}</span>
          <h1>Закупка</h1>
          <p>{readOnly ? "Лист закупки открыт вам только на просмотр: что свободно и что едет." : "Разберите новые позиции и заполните фактические данные поставки."}</p>
        </div>
        <button className="secondary" onClick={() => void reload()} disabled={interactionLocked}>
          {refreshing ? "Обновляем…" : "Обновить"}
        </button>
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}

      <div className="queue-summary">
        <span><strong>{available.length}</strong> доступно</span>
        <span><strong>{mine.length}</strong> {readOnly ? "в работе" : "у меня"}</span>
      </div>

      <section className="panel table-panel">
        <div className="panel-heading"><h2>Неразобранные</h2>{!readOnly && <span>Берите только то, что будете вести</span>}</div>
        <DataTable
          label="Неразобранные"
          rows={available}
          columns={readOnly ? availableColumns : [...availableColumns, {
            key: "take", title: "", value: () => "",
            render: (line) => <button className="secondary small" disabled={interactionLocked} onClick={() => void take(line)}>{busyId === line.id ? "Берём…" : "Взять"}</button>,
          }]}
          rowKey={(line) => line.id}
          storageKey="procurement-available"
          emptyText="Свободных позиций нет"
          bulkActions={readOnly ? [] : [{ label: "Взять в работу", run: takeMany, disabled: () => interactionLocked }]}
        />
        {availableCursor && (
          <button className="secondary" disabled={interactionLocked} onClick={() => void loadMore("available", availableCursor)}>
            {loadingMore === "available" ? "Загружаем…" : "Ещё неразобранные"}
          </button>
        )}
      </section>

      {readOnly && (
        <section className="panel table-panel">
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
        <div className="panel-heading"><h2>Мои позиции</h2><span>Изменения защищены версией строки</span></div>
        <div className="inline-action">
          <small>Готово к запросу: {readyCount}. Запросы группируются по поставщику и типу задачи, № заказа PO-NNN.</small>
          <button className="primary small" disabled={interactionLocked || readyCount === 0} onClick={() => void buildRequests()}>
            {busyId === "supplier-requests" ? "Формируем…" : "Сформировать запросы поставщикам"}
          </button>
          <button type="button" className="secondary small" disabled={interactionLocked} onClick={() => setFilling((current) => !current)}>Заполнить данные</button>
        </div>
        {filling && (
          <ProcurementFillForm
            units={units}
            onClose={() => setFilling(false)}
            onSaved={(message) => {
              setNotice(message);
              void reload(true);
            }}
          />
        )}
        {mine.some(payable) && (
          <section className="panel table-panel">
            <div className="panel-heading"><h2>К оплате</h2><span>Отметьте оплаченные счета — строки уйдут «В пути» в приёмку</span></div>
            <DataTable
              label="К оплате"
              rows={mine.filter(payable)}
              columns={toPayColumns}
              rowKey={(line) => line.id}
              bulkActions={[{ label: "Оплачено → В пути", run: markPaidMany, disabled: () => interactionLocked }]}
            />
          </section>
        )}
        {orders.map((order) => (
          <article className="panel procurement-card" key={order.order_number}>
            <div className="procurement-title">
              <div><strong>{order.order_number} · {order.supplier}</strong><small>{order.task_type} · от {order.order_date} · позиций: {order.line_ids.length}</small></div>
            </div>
            <pre className="supplier-letter">{order.letter}</pre>
          </article>
        ))}
        {kpReference && (
          <KpForm
            key={kpReference}
            reference={kpReference}
            onClose={() => setKpReference("")}
            onSaved={(message, failure) => {
              setNotice(message);
              setError(failure);
              setKpReference("");
              void reload(true);
            }}
          />
        )}
        {mine.map((line) => (
          <ProcurementCard
            key={`${line.id}:${line.version}`}
            line={line}
            busy={busyId === line.id}
            disabled={interactionLocked}
            onSave={save}
            onAdvance={advance}
            onOpenKp={setKpReference}
            units={units}
          />
        ))}
        {!mine.length && <div className="panel empty">Пока ничего не взято в работу.</div>}
        {mineCursor && (
          <button className="secondary" disabled={interactionLocked} onClick={() => void loadMore("mine", mineCursor)}>
            {loadingMore === "mine" ? "Загружаем…" : "Ещё мои"}
          </button>
        )}
      </section>}
    </section>
  );
}

function ProcurementCard({
  line,
  busy,
  disabled,
  onSave,
  onAdvance,
  onOpenKp,
  units = DEFAULT_UNITS,
}: {
  line: ProcurementLine;
  busy: boolean;
  disabled: boolean;
  units?: string[];
  onOpenKp: (reference: string) => void;
  onSave: (line: ProcurementLine, draft: FactsDraft) => Promise<void>;
  onAdvance: (
    line: ProcurementLine,
    operation: () => Promise<unknown>,
    message: string,
  ) => Promise<void>;
}) {
  const [draft, setDraft] = useState(() => draftFrom(line));
  const [invoiceUrl, setInvoiceUrl] = useState(line.invoice_url ?? "");
  // № and date of an uploaded file; empty keeps what the money form saved (uploadInvoice setIf).
  const [invoiceFile, setInvoiceFile] = useState(false);
  const [invoiceNumber, setInvoiceNumber] = useState("");
  const [invoiceDate, setInvoiceDate] = useState("");
  const [expectedArrival, setExpectedArrival] = useState(line.expected_arrival ?? "");
  const moneyMissing = line.pay_missing.filter((name) => name !== "ОЖИД. ДАТА");

  function field(name: keyof FactsDraft, value: string) {
    setDraft((current) => ({ ...current, [name]: value }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void onSave(line, draft).catch(() => undefined);
  }

  return (
    <article className="panel procurement-card">
      <div className="procurement-title">
        <div><strong>{line.legacy_id} · {line.calculated_name}</strong><small>{line.designation} · {line.calculated_quantity} {line.calculated_unit}</small></div>
        <span className="status">{line.status}</span>
      </div>
      {line.status === "В работе" && <form onSubmit={submit}><div className="facts-grid">
        <label>Поставщик *<input value={draft.supplier} onChange={(e) => field("supplier", e.target.value)} required /></label>
        <label>Площадка<input value={draft.site_name} onChange={(e) => field("site_name", e.target.value)} /></label>
        <label>Фактическое количество *<input type="number" min="0.000001" step="any" value={draft.actual_quantity} onChange={(e) => field("actual_quantity", e.target.value)} required /></label>
        <label>Единица *<select value={draft.actual_unit} onChange={(e) => field("actual_unit", e.target.value)} required>
          <option value="">—</option>
          {(units.includes(draft.actual_unit) || !draft.actual_unit ? units : [draft.actual_unit, ...units]).map((unit) => <option key={unit} value={unit}>{unit}</option>)}
        </select></label>
        <label>Артикул<input value={draft.design_article} onChange={(e) => field("design_article", e.target.value)} /></label>
        <label>Уточнённое наименование<input value={draft.calculated_name} onChange={(e) => field("calculated_name", e.target.value)} /></label>
        <label className="wide">Ссылка<input type="url" value={draft.link} onChange={(e) => field("link", e.target.value)} placeholder="https://…" /></label>
      </div>
      <div className="card-footer"><small>Версия {line.version} · план {line.planned_date}</small><button className="primary small" disabled={disabled}>{busy ? "Сохраняем…" : "Сохранить"}</button></div></form>}
      {REPLACEMENT_STATUSES.has(line.status) && (
        <ReplacementControl line={line} busy={busy} disabled={disabled} onAdvance={onAdvance} />
      )}
      {REPLACEMENT_STATUSES.has(line.status) && (
        <TraceabilityForm line={line} busy={busy} disabled={disabled} onAdvance={onAdvance} />
      )}
      {line.permission_number && <ReplacementPermissionPrint line={line} />}
      {line.status === "Готов к запросу" && (
        <small className="hint">Поставщик: {line.supplier} — строка войдёт в запрос поставщику при формировании.</small>
      )}
      {line.order_number && (
        <small className="hint">Заказ {line.order_number} от {line.order_date}</small>
      )}
      {line.status === "Готов к запросу" && line.replacement_open && (
        <small className="hint">Есть незакрытая замена — заказ этого поставщика не сформируется до решения «Разрешено».</small>
      )}
      {line.status === "Жду счет" && (
        <>
          {line.order_number && (
            <div className="card-footer">
              <small>Все строки заказа {line.order_number} одной формой, можно из файла КП</small>
              <button type="button" className="secondary small" disabled={disabled} onClick={() => onOpenKp(line.order_number ?? "")}>Внести КП</button>
            </div>
          )}
          <MoneyForm line={line} busy={busy} disabled={disabled} onAdvance={onAdvance} />
          <MoneySummary line={line} />
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
      )}
      {line.status === "В оплату" && (
        <>
          {line.pay_missing.length > 0 ? (
            <>
              <MoneyForm line={line} busy={busy} disabled={disabled} onAdvance={onAdvance} />
              <small className="hint">Оплата закрыта, пока не заполнено: {line.pay_missing.join(", ")}</small>
            </>
          ) : (
            <MoneySummary line={line} />
          )}
          <PaymentAction line={line} busy={busy} disabled={disabled || line.pay_missing.length > 0} onAdvance={onAdvance} />
        </>
      )}
      {line.status === "Согласование" && line.is_backorder && (
        <>
          <p className="money-summary">
            <small>Дозавоз по {line.legacy_id.replace(/-Д\d*$/, "")}: недовоз уже оплачен по счёту {line.invoice_number ? `№ ${line.invoice_number}` : line.invoice_url}. Поставщик подтвердил досыл — укажите новую дату; вернул деньги — отмените, остаток уйдёт новой заявкой.</small>
          </p>
          <MoneySummary line={line} />
          <form className="inline-action" onSubmit={(event) => { event.preventDefault(); void onAdvance(line, () => procurementApi.confirmBackorder(line.id, line.version, expectedArrival), `${line.legacy_id}: досыл подтверждён, передано в приёмку`).catch(() => undefined); }}>
            <label>Новая ожидаемая дата<input type="date" required value={expectedArrival} onChange={(event) => setExpectedArrival(event.target.value)} /></label>
            <button className="primary small" disabled={disabled}>{busy ? "Проводим…" : "Досыл подтверждён → в пути"}</button>
            <button type="button" className="secondary small" disabled={disabled} onClick={() => void onAdvance(line, () => procurementApi.cancelBackorder(line.id, line.version), `${line.legacy_id}: дозавоз отменён, остаток ушёл новой заявкой`).catch(() => undefined)}>Отменить дозавоз</button>
          </form>
        </>
      )}
      {!line.is_backorder && OFFLINE_FROM.includes(line.status) && (
        <OfflineActions line={line} busy={busy} disabled={disabled} onAdvance={onAdvance} />
      )}
      {line.status === "В пути" && (
        <>
          <div className="success-banner">Позиция передана в приёмку · ожидается {line.expected_arrival}</div>
          <MoneySummary line={line} />
          {line.exchange_rate_manual && (
            <div className="card-footer">
              <small>Курс введён вручную — сверьте с ЦБ, когда он ответит</small>
              <button className="secondary small" disabled={disabled} onClick={() => void onAdvance(line, () => procurementApi.reconcileRate(line.id, line.version), `${line.legacy_id}: курс сверен с ЦБ`).catch(() => undefined)}>{busy ? "Сверяем…" : "Сверить курс с ЦБ"}</button>
            </div>
          )}
        </>
      )}
      {line.invoice_url && line.status !== "Жду счет" && line.status !== "Отменено" && (
        <InvoiceReplaceForm line={line} busy={busy} disabled={disabled} onAdvance={onAdvance} />
      )}
      <SplitAction line={line} busy={busy} disabled={disabled} onAdvance={onAdvance} />
    </article>
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

const REPLACEMENT_STATUSES = new Set(["В работе", "Готов к запросу", "Жду счет", "В оплату"]);

type ReplacementChoice = "" | "no" | "open" | "Разрешено" | "Запрещено";

function replacementChoice(line: ProcurementLine): ReplacementChoice {
  if (line.replacement === null) return "";
  if (!line.replacement) return "no";
  return line.replacement_decision ?? "open";
}

function ReplacementControl({ line, busy, disabled, onAdvance }: {
  line: ProcurementLine;
  busy: boolean;
  disabled: boolean;
  onAdvance: Advance;
}) {
  function choose(choice: ReplacementChoice) {
    if (!choice || choice === replacementChoice(line)) return;
    const replacement = choice !== "no";
    const decision = choice === "Разрешено" || choice === "Запрещено" ? choice : null;
    void onAdvance(
      line,
      () => procurementApi.setReplacement(line.id, line.version, replacement, decision),
      `${line.legacy_id}: замена — ${replacement ? decision ?? "ждём решения" : "нет"}`,
    ).catch(() => undefined);
  }

  return (
    <div className="inline-action">
      <label>Замена
        <select value={replacementChoice(line)} disabled={disabled || busy} onChange={(event) => choose(event.target.value as ReplacementChoice)}>
          <option value="" disabled>не отмечено</option>
          <option value="no">Нет</option>
          <option value="open">Да — ждём решения</option>
          <option value="Разрешено">Да — Разрешено</option>
          <option value="Запрещено">Да — Запрещено</option>
        </select>
      </label>
    </div>
  );
}

// «Лист закупки» TRACE_COLUMNS.PURCHASE: ИЗГОТОВИТЕЛЬ, СТРАНА, ТИП ДОК., № / ДАТА ДОК. КАЧЕСТВА (+ СТАНДАРТ) —
// уходят в приёмку со снимком поставки; пустая СТРАНА подставляется по изготовителю из справочника.
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

  function field(name: keyof ProcurementTraceabilityInput, value: string) {
    setDraft((current) => ({ ...current, [name]: name === "quality_document_date" ? value || null : value }));
  }

  function save(event: FormEvent) {
    event.preventDefault();
    void onAdvance(line, () => procurementApi.setTraceability(line.id, line.version, draft), `${line.legacy_id}: прослеживаемость сохранена`).catch(() => undefined);
  }

  return (
    <details className="build-config">
      <summary>Прослеживаемость: изготовитель, страна, документ качества</summary>
      <form onSubmit={save}>
        <div className="form-grid">
          <label>Изготовитель<input value={draft.manufacturer} onChange={(e) => field("manufacturer", e.target.value)} /></label>
          <label>Страна<input value={draft.country} onChange={(e) => field("country", e.target.value)} placeholder="по изготовителю" /></label>
          <label>Тип док.<input value={draft.quality_document_type} onChange={(e) => field("quality_document_type", e.target.value)} /></label>
          <label>№ док. качества<input value={draft.quality_document_number} onChange={(e) => field("quality_document_number", e.target.value)} /></label>
          <label>Дата док. качества<input type="date" value={draft.quality_document_date ?? ""} onChange={(e) => field("quality_document_date", e.target.value)} /></label>
          <label>Стандарт<input value={draft.standard} onChange={(e) => field("standard", e.target.value)} /></label>
        </div>
        <div className="card-footer"><small>Уходит в приёмку при оплате</small><button className="secondary small" disabled={disabled || busy}>Сохранить прослеживаемость</button></div>
      </form>
    </details>
  );
}

function ReplacementPermissionPrint({ line }: { line: ProcurementLine }) {
  const [error, setError] = useState("");

  async function print() {
    setError("");
    try {
      const { sheet } = await procurementApi.replacementPermissionSheet(line.id);
      openPrintSheet(sheet);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сформировать разрешение");
    }
  }

  return (
    <div className="inline-action">
      <button type="button" className="secondary small" onClick={() => void print()}>
        Печать «Разрешение на замену» {line.permission_number}
      </button>
      {error && <div className="error-banner" role="alert">{error}</div>}
    </div>
  );
}

type Advance = (line: ProcurementLine, operation: () => Promise<unknown>, message: string) => Promise<void>;

// «Согласование» / «Отменено» stand outside the linear order (SUPPLYMAP `PUR_STATUS_OFFLINE`): any stage before payment.
const OFFLINE_FROM = ["В работе", "Готов к запросу", "Жду счет", "В оплату", "Согласование"];

function OfflineActions({ line, busy, disabled, onAdvance }: {
  line: ProcurementLine;
  busy: boolean;
  disabled: boolean;
  onAdvance: Advance;
}) {
  const parked = line.status === "Согласование";

  function cancel() {
    if (!window.confirm(`Отменить ${line.legacy_id}? Отмена окончательная.`)) return;
    void onAdvance(line, () => procurementApi.cancelLine(line.id, line.version), `${line.legacy_id}: строка отменена`).catch(() => undefined);
  }

  return (
    <div className="card-footer">
      <small>{parked ? "Ждёт одобрения — заказ не формируется" : "Нужно одобрение или позиция больше не нужна?"}</small>
      {parked ? (
        <button className="primary small" disabled={disabled} onClick={() => void onAdvance(line, () => procurementApi.resumeFromApproval(line.id, line.version), `${line.legacy_id}: одобрено, строка вернулась в работу`).catch(() => undefined)}>{busy ? "Проводим…" : "Одобрено — продолжить"}</button>
      ) : (
        <button className="secondary small" disabled={disabled} onClick={() => void onAdvance(line, () => procurementApi.putOnApproval(line.id, line.version), `${line.legacy_id}: на согласовании`).catch(() => undefined)}>На согласование</button>
      )}
      <button type="button" className="secondary small" disabled={disabled} onClick={cancel}>Отменить строку</button>
    </div>
  );
}

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

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

/** SUPPLYMAP «Внести КП» (Kp.gs, KpForm.html): every «Жду счет» line of one supplier order at once. */
function KpForm({ reference, onClose, onSaved }: {
  reference: string;
  onClose: () => void;
  onSaved: (message: string, failure: string) => void;
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
        <h2>Внести КП · заказ {reference}</h2>
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
