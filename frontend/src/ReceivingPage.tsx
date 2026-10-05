import { useEffect, useState, type FormEvent } from "react";
import { receivingApi, type ReceivingLine } from "./api";
import { DataTable, type DataColumn, type DataPreset } from "./DataTable";
import { formatDate } from "./format";
import { openPrintSheet } from "./print";
import { SupplierReturnPanel } from "./SupplierReturnPanel";
import { BUSY_TITLE, PlaceHint, usePlaceHint } from "./WarehousePlaceHint";

const MANUAL_CHECKS = [
  "Механическое повреждение / брак",
  "Не тот товар (несоответствие счету)",
];

const COLUMNS: DataColumn<ReceivingLine>[] = [
  { key: "legacy_id", title: "ID", value: (line) => line.legacy_id },
  { key: "name", title: "Наименование", value: (line) => itemTitle(line), wrap: true, width: 210 },
  { key: "supplier", title: "Поставщик", value: (line) => line.supplier, filter: true },
  { key: "order", title: "№ заказа", value: (line) => line.order_number ?? "" },
  { key: "expected", title: "Ожид. дата", value: (line) => line.expected_arrival ?? "" },
  { key: "purpose", title: "Назначение", value: (line) => line.purpose, filter: true, wrap: true, width: 140 },
  { key: "ordered", title: "Заказано", value: (line) => line.ordered_quantity, numeric: true, render: (line) => `${line.ordered_quantity} ${line.unit}` },
  { key: "status", title: "Статус", value: (line) => line.status, filter: true, render: (line) => <span className="status">{line.status}</span> },
  { key: "document", title: "УПД", value: (line) => [line.document_number, formatDate(line.document_date)].filter(Boolean).join(" от "), wrap: true, width: 100 },
  { key: "grn", title: "ГРН", value: (line) => line.grn ? `${line.grn}${line.package ? ` / ${line.package}` : ""}` : "" },
  { key: "location", title: "Место", value: (line) => line.storage_location ?? "", hidden: true },
  { key: "warehouse", title: "Склад", value: (line) => line.warehouse, filter: true, hidden: true },
  { key: "series", title: "Серия", value: (line) => line.series, hidden: true },
  { key: "fact", title: "Факт", value: (line) => line.fact_quantity ?? "", numeric: true, hidden: true },
  { key: "verdict", title: "Вердикт", value: (line) => line.verdict ?? "", filter: true, hidden: true },
];

/** Артикул · наименование; without a design article the order carries the name in both — show it once. */
function itemTitle(line: ReceivingLine) {
  return [...new Set([line.article, line.name].map((value) => (value ?? "").trim()).filter(Boolean))].join(" · ");
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

/** SUPPLYMAP «Документ УПД/М-15 (файл)»: the scan goes to the ERP storage, its link fills the field.
 *  A link to a document kept elsewhere can still be typed by hand. */
function DocumentField({ lineId, kind, label, value, required, disabled, hint, inline, onChange }: {
  lineId: string;
  kind: "upd" | "quality";
  label: string;
  value: string;
  required?: boolean;
  disabled: boolean;
  hint?: string;
  /** Кнопка «Файл» в одну строку с полем (панель «Внести УПД»). */
  inline?: boolean;
  onChange: (value: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  async function upload(file: File) {
    setBusy(true);
    setNote("");
    setError("");
    try {
      const result = await receivingApi.uploadFile(lineId, kind, {
        name: file.name, type: file.type || "application/octet-stream", base64: await readBase64(file),
      });
      onChange(result.url);
      setNote(result.file_name);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Файл не загружен");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={inline ? "document-field inline" : "document-field"}>
      <label>{label}{required ? " *" : ""}
        <input type="text" inputMode="url" placeholder="Файл или ссылка" required={required} value={value} onChange={(event) => { setNote(""); onChange(event.target.value); }} />
      </label>
      <div className="document-actions">
        <label className="secondary small file-button">{busy ? "Загружаем…" : "Файл PDF/фото"}
          <input type="file" accept=".pdf,.jpg,.jpeg,.png" hidden disabled={disabled || busy} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file); }} />
        </label>
        {value.trim() && <a href={value.trim()} target="_blank" rel="noreferrer">открыть</a>}
        {note && <small className="hint">{note}</small>}
      </div>
      {!value.trim() && hint && <small className="hint">{hint}</small>}
      {error && <small className="danger-text" role="alert">{error}</small>}
    </div>
  );
}

const PRESETS: DataPreset<ReceivingLine>[] = [
  { label: "В пути", test: (line) => line.status === "В пути" },
  { label: "Ожидание", test: (line) => line.status === "Ожидание" },
  { label: "Карантин", test: (line) => line.status === "Карантин" },
  { label: "Принято, не разложено", test: (line) => line.status === "Принято" && !line.warehouse_event_emitted },
  { label: "На складе", test: (line) => line.warehouse_event_emitted },
  { label: "Возврат", test: (line) => line.status === "Возврат" },
  { label: "Архив ВК", test: (line) => line.status === "Архив ВК" },
];

const GROUPS: Record<string, ((line: ReceivingLine) => string) | undefined> = {
  "": undefined,
  supplier: (line) => line.supplier || "Без поставщика",
  order: (line) => line.order_number ? `Заказ ${line.order_number}` : "Без номера заказа",
};

const pluralLines = (count: number) => `${count} ${count % 10 === 1 && count % 100 !== 11 ? "позиция" : count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14) ? "позиции" : "позиций"}`;

/** Заголовок группы «по заказу»: заказ · поставщик первой строки. */
function orderGroupLabel(name: string, rows: ReceivingLine[]) {
  const supplier = rows[0]?.supplier;
  return supplier ? `${name} · ${supplier}` : name;
}

/** «УПД № 771 от 04.10.2026 внесён: 6 позиций»; без номера и даты — «Файл УПД внесён…». */
export function updDoneText(payload: { url: string; number: string; date: string }, count: number) {
  const number = payload.number.trim();
  const named = number || payload.date;
  const head = named
    ? `УПД${number ? ` № ${number}` : ""}${payload.date ? ` от ${formatDate(payload.date)}` : ""}`
    : "Файл УПД";
  return `${head} внесён: ${pluralLines(count)}`;
}

/** «Внести УПД»: один документ на выбранные позиции заказа (остальные можно отметить другим УПД). */
function UpdPanel({ rows, busy, onSubmit, onClose }: {
  rows: ReceivingLine[];
  busy: boolean;
  onSubmit: (payload: { url: string; number: string; date: string }) => void;
  onClose: () => void;
}) {
  const [url, setUrl] = useState("");
  const [number, setNumber] = useState("");
  const [date, setDate] = useState("");
  const ids = rows.slice(0, 8).map((line) => line.legacy_id).join(", ");
  return (
    <form className="panel upd-panel" onSubmit={(event) => { event.preventDefault(); onSubmit({ url, number, date }); }}>
      <div className="upd-head">
        <h2>УПД для {rows.length} позиций</h2>
        <span className="upd-ids">{ids}{rows.length > 8 ? ` и ещё ${rows.length - 8}` : ""}</span>
      </div>
      <div className="upd-row">
        <DocumentField lineId={rows[0].id} kind="upd" label="Документ УПД/М-15" value={url} disabled={busy} inline onChange={setUrl} />
        <label>№ УПД/М-15<input value={number} disabled={busy} onChange={(event) => setNumber(event.target.value)} /></label>
        <label>Дата УПД/М-15<input type="date" value={date} disabled={busy} onChange={(event) => setDate(event.target.value)} /></label>
        <div className="upd-actions">
          <button type="submit" className="primary small" disabled={busy || !(url.trim() || number.trim() || date)}>Внести УПД в {pluralLines(rows.length)}</button>
          <button type="button" className="secondary small" disabled={busy} onClick={onClose}>Отмена</button>
        </div>
      </div>
    </form>
  );
}

/** `readOnly` — «режим просмотра» (ID-6): the queue is read; no inspection, placement or new ВК
 *  (SUPPLYMAP: «Приёмка — просмотр» has no canReceive). */
export function ReceivingPage({ readOnly = false }: { readOnly?: boolean } = {}) {
  const [lines, setLines] = useState<ReceivingLine[]>([]);
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [returning, setReturning] = useState(false);
  const [group, setGroup] = useState("order");
  const [updRows, setUpdRows] = useState<ReceivingLine[] | null>(null);
  const [notice, setNotice] = useState("");

  async function reload(after?: string) {
    if (loading || busy) return;
    setLoading(true);
    setError("");
    try {
      const result = await receivingApi.list(after);
      setLines((current) => after
        ? [...new Map([...current, ...result.lines].map((line) => [line.id, line])).values()]
        : result.lines);
      setNextCursor(result.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить приёмку");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void reload(); }, []);

  async function inspect(line: ReceivingLine, payload: Record<string, unknown>) {
    if (busy || loading) return;
    setBusy(line.id);
    setError("");
    try {
      const done = await receivingApi.inspect(line.id, { version: line.version, ...payload });
      const assigned = (done.lines ?? [done.line]).filter((item) => item.grn);
      if (assigned.length) setNotice(`Присвоены ГРН: ${assigned.map((item) => `${item.legacy_id} — ${item.grn}`).join("; ")}`);
      const result = await receivingApi.list();
      setLines(result.lines);
      setNextCursor(result.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Входной контроль не выполнен");
      throw reason;
    } finally {
      setBusy("");
    }
  }

  async function place(line: ReceivingLine, location: string) {
    if (busy || loading) return;
    setBusy(line.id);
    setError("");
    try {
      await receivingApi.place(line.id, line.version, location);
      const result = await receivingApi.list();
      setLines(result.lines);
      setNextCursor(result.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Размещение не выполнено");
      throw reason;
    } finally {
      setBusy("");
    }
  }

  async function unpack(line: ReceivingLine, quantities: string[]) {
    if (busy || loading) return;
    setBusy(line.id);
    setError("");
    try {
      const result = await receivingApi.unpack(line.id, line.version, quantities);
      setNotice(`Разукомплектовано: ${result.lines.map((item) => `${item.legacy_id} — пакет ${item.package}, ${item.fact_quantity} ${item.unit}`).join("; ")}`);
      const fresh = await receivingApi.list();
      setLines(fresh.lines);
      setNextCursor(fresh.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Разукомплектовка не выполнена");
      throw reason;
    } finally {
      setBusy("");
    }
  }

  /** «Скопировать ГРН»: список присвоенных ГРН — как SUPPLYMAP показывает их после «Внести» для маркировки. */
  async function copyGrns(selected: ReceivingLine[]) {
    const text = selected.filter((line) => line.grn).map((line) => `${line.legacy_id}\t${line.grn}${line.package ? ` / ${line.package}` : ""}`).join("\n");
    if (!text) { setNotice("У выбранных строк ещё нет ГРН"); return; }
    try {
      await navigator.clipboard.writeText(text);
      setNotice(`ГРН скопированы (${text.split("\n").length})`);
    } catch {
      setNotice(text);
    }
  }

  async function attachDocuments(selected: ReceivingLine[], payload: { url: string; number: string; date: string }) {
    setBusy("upd");
    setError("");
    try {
      await receivingApi.attachDocuments(
        selected.map((line) => ({ id: line.id, version: line.version })),
        { shipping_document_url: payload.url.trim() || null, document_number: payload.number.trim(), document_date: payload.date || null },
      );
      setBusy("");
      setNotice(updDoneText(payload, selected.length));
      setUpdRows(null);
      const result = await receivingApi.list();
      setLines(result.lines);
      setNextCursor(result.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "УПД не внесён");
    } finally {
      setBusy("");
    }
  }

  async function openVkRevision(line: ReceivingLine) {
    if (busy || loading) return;
    if (!window.confirm(`Строка ${line.legacy_id} уйдёт в «Архив ВК» (акты сохранятся как история). Появится ревизия с контекстом заказа и УПД — по ней проведёте приёмку заново. Продолжить?`)) return;
    setBusy(line.id);
    setError("");
    try {
      await receivingApi.openVkRevision(line.id, line.version);
      const result = await receivingApi.list();
      setLines(result.lines);
      setNextCursor(result.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Новый ВК не открыт");
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="page">
      <header className="page-head">
        <h1>Приёмка</h1><span className="page-count">{lines.length}</span>
        <div className="page-actions">
          {!readOnly && <button className="secondary" disabled={Boolean(busy)} onClick={() => setReturning((current) => !current)}>Возврат поставщику</button>}
          <button className="secondary" disabled={loading || Boolean(busy)} onClick={() => void reload()}>{loading ? "Обновляем…" : "Обновить"}</button>
        </div>
      </header>
      {readOnly && <p className="page-sub">Приёмка открыта вам только на просмотр.</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {returning && <SupplierReturnPanel onDone={() => void reload()} onClose={() => setReturning(false)} />}
      {updRows && <UpdPanel rows={updRows} busy={Boolean(busy)} onSubmit={(payload) => void attachDocuments(updRows, payload)} onClose={() => setUpdRows(null)} />}
      {notice && <div className="success-banner" role="status">{notice}</div>}
      <DataTable
        rows={lines}
        columns={COLUMNS}
        rowKey={(line) => line.id}
        storageKey="receiving-v2"
        label="Очередь приёмки"
        presets={PRESETS}
        groupBy={GROUPS[group]}
        groupLabel={group === "order" ? orderGroupLabel : undefined}
        exportName="Приёмка"
        emptyText={loading ? "Загружаем…" : "Поставок для приёмки пока нет."}
        bulkActions={[
          ...(readOnly ? [] : [{
            label: "Внести УПД",
            run: async (rows: ReceivingLine[]) => { setNotice(""); setUpdRows(rows); },
            disabled: (rows: ReceivingLine[]) => rows.some((line) => line.status !== "В пути" && line.status !== "Ожидание"),
          }]),
          { label: "Скопировать ГРН", run: copyGrns, hidden: (rows) => !rows.some((line) => line.grn) },
        ]}
        toolbar={(
          <label className="inline-select">Группировать<select value={group} onChange={(event) => setGroup(event.target.value)}><option value="">нет</option><option value="supplier">по поставщику</option><option value="order">по заказу</option></select></label>
        )}
        renderDetail={(line) => (
          <ReceivingCard
            key={`${line.id}:${line.version}`}
            line={line}
            busy={busy === line.id}
            disabled={Boolean(busy) || loading}
            onInspect={inspect}
            onPlace={place}
            onUnpack={unpack}
            onOpenVkRevision={openVkRevision}
            readOnly={readOnly}
          />
        )}
      />
      {nextCursor && <button className="secondary" disabled={loading || Boolean(busy)} onClick={() => void reload(nextCursor)}>Показать ещё</button>}
    </section>
  );
}

function ReceivingCard({
  line,
  busy,
  disabled,
  onInspect,
  onPlace,
  onUnpack,
  onOpenVkRevision,
  readOnly,
}: {
  line: ReceivingLine;
  busy: boolean;
  disabled: boolean;
  onInspect: (line: ReceivingLine, payload: Record<string, unknown>) => Promise<void>;
  onPlace: (line: ReceivingLine, location: string) => Promise<void>;
  onUnpack: (line: ReceivingLine, quantities: string[]) => Promise<void>;
  onOpenVkRevision: (line: ReceivingLine) => Promise<void>;
  readOnly: boolean;
}) {
  const [fact, setFact] = useState(line.fact_quantity ?? line.ordered_quantity);
  const [documentQty, setDocumentQty] = useState(line.document_quantity ?? line.ordered_quantity);
  const [shippingDocument, setShippingDocument] = useState(line.shipping_document_url ?? "");
  const [qualityDocument, setQualityDocument] = useState(line.quality_document_url ?? "");
  const [expiry, setExpiry] = useState(line.expiry_date ?? "");
  const [disposition, setDisposition] = useState(line.disposition ?? "");
  const [manual, setManual] = useState<string[]>(
    line.nonconformities.filter((item) => MANUAL_CHECKS.includes(item)),
  );
  const [prefix, setPrefix] = useState("");
  const [historical, setHistorical] = useState(false);
  const [documentDate, setDocumentDate] = useState(line.document_date ?? "");
  const [documentNumber, setDocumentNumber] = useState(line.document_number ?? "");
  const [documentName, setDocumentName] = useState(line.document_name ?? "");
  const [manufactureDate, setManufactureDate] = useState(line.manufacture_date ?? "");
  // Изготовитель, страна и документ качества для акта ВК; из заказа, приёмщик уточняет.
  const [quality, setQuality] = useState({
    manufacturer: line.manufacturer ?? "",
    country: line.country ?? "",
    quality_document_type: line.quality_document_type ?? "",
    quality_document_number: line.quality_document_number ?? "",
    quality_document_date: line.quality_document_date ?? "",
    standard: line.standard ?? "",
  });
  const [note, setNote] = useState(line.inspection_note ?? "");
  const [description, setDescription] = useState("");
  // RC-13: партии изготовителя одной поставки; пусто — обычная приёмка одной строкой.
  const [batches, setBatches] = useState<Batch[]>([]);
  const [packages, setPackages] = useState<string[]>([]);
  const [series, setSeries] = useState(line.series);
  // МАССА 1 ШТ: any one of 1/10/100 pieces; the server prefills the one it was likely weighed with.
  const [mass, setMass] = useState(line.mass_fields ?? { mass_1: "", mass_10: "", mass_100: "" });
  const [location, setLocation] = useState(line.storage_location ?? "");
  const placeHint = usePlaceHint(line.warehouse, location);

  function submitInspection(event: FormEvent) {
    event.preventDefault();
    void onInspect(line, {
      fact_quantity: fact || null,
      document_quantity: documentQty || null,
      shipping_document_url: shippingDocument.trim() || null,
      quality_document_url: qualityDocument.trim() || null,
      expiry_date: expiry || null,
      manual_nonconformities: manual,
      disposition: disposition || null,
      manual_grn_prefix: prefix || null,
      series: series.trim(),
      ...mass,
      historical,
      document_date: documentDate || null,
      document_number: documentNumber.trim(),
      document_name: documentName.trim(),
      manufacture_date: manufactureDate || null,
      manufacturer: quality.manufacturer.trim(),
      country: quality.country.trim(),
      quality_document_type: quality.quality_document_type.trim(),
      quality_document_number: quality.quality_document_number.trim(),
      quality_document_date: quality.quality_document_date || null,
      standard: quality.standard.trim(),
      note: note.trim(),
      nonconformity_description: description.trim(),
      ...(batches.length >= 2 ? { batches: batches.map((batch) => ({
        document_quantity: batch.document_quantity || null,
        fact_quantity: batch.fact_quantity || null,
        series: batch.series.trim(),
        expiry_date: batch.expiry_date || null,
        manufacture_date: batch.manufacture_date || null,
      })) } : {}),
    }).catch(() => undefined);
  }

  const inspectable = !readOnly && ["В пути", "Ожидание", "Карантин"].includes(line.status);
  const placeable = !readOnly && line.status === "Принято" && !line.warehouse_event_emitted;
  const unpackable = placeable && Boolean(line.grn) && !line.package && !line.storage_location;
  const batchMode = batches.length >= 2;

  function startBatches() {
    setBatches((current) => current.length
      ? [...current, emptyBatch()]
      : [{ ...emptyBatch(), document_quantity: documentQty, fact_quantity: fact, series, expiry_date: expiry, manufacture_date: manufactureDate }, emptyBatch()]);
  }

  function editBatch(index: number, patch: Partial<Batch>) {
    setBatches((current) => current.map((batch, position) => position === index ? { ...batch, ...patch } : batch));
  }
  const revisable = !readOnly && Boolean(line.verdict) && line.status !== "Архив ВК" && !line.warehouse_event_emitted;
  return (
    <article className="panel receiving-card">
      <div className="procurement-title">
        <div><strong>{line.legacy_id} · {itemTitle(line)}</strong><small>{line.supplier} · заказано {line.ordered_quantity} {line.unit} · {line.warehouse}</small></div>
        <span className="status">{line.status}</span>
      </div>
      {line.nonconformities.length > 0 && <p className="nonconformity">{line.nonconformities.join(" · ")}</p>}
      {line.nonconformity_description && <p className="nonconformity">{line.nonconformity_description}</p>}
      {(line.document_number || line.inspection_note) && <p className="hint">{[line.document_number && `УПД ${line.document_number}${line.document_date ? ` от ${line.document_date}` : ""}`, line.inspection_note].filter(Boolean).join(" · ")}</p>}
      {Number(line.backorder_quantity) > 0 && <p className="nonconformity">Недовоз {line.backorder_quantity} {line.unit} — оплаченный остаток ушёл в закупку дозавозом</p>}
      {inspectable && (
        <form onSubmit={submitInspection}>
          <div className="facts-grid">
            <DocumentField lineId={line.id} kind="upd" label="Документ УПД/М-15" value={shippingDocument} disabled={disabled} hint="Приложите скан первым делом. Без УПД позиция уйдёт в «Ожидание» с несоответствием документов." onChange={setShippingDocument} />
            <label>№ УПД/М-15<input value={documentNumber} onChange={(event) => setDocumentNumber(event.target.value)} /></label>
            <label>Дата УПД/М-15<input type="date" required={historical} value={documentDate} onChange={(event) => setDocumentDate(event.target.value)} /></label>
            <label className="wide">Наименование по УПД<input value={documentName} onChange={(event) => setDocumentName(event.target.value)} /></label>
            <label>Изготовитель<input value={quality.manufacturer} onChange={(event) => setQuality({ ...quality, manufacturer: event.target.value })} /></label>
            <label>Страна<input value={quality.country} onChange={(event) => setQuality({ ...quality, country: event.target.value })} /></label>
            <label>Тип док. качества<input value={quality.quality_document_type} onChange={(event) => setQuality({ ...quality, quality_document_type: event.target.value })} /></label>
            <label>№ док. качества<input value={quality.quality_document_number} onChange={(event) => setQuality({ ...quality, quality_document_number: event.target.value })} /></label>
            <label>Дата док. качества<input type="date" value={quality.quality_document_date} onChange={(event) => setQuality({ ...quality, quality_document_date: event.target.value })} /></label>
            <label>Стандарт<input value={quality.standard} onChange={(event) => setQuality({ ...quality, standard: event.target.value })} /></label>
            {!batchMode && <>
              <label title="Можно заполнить только одно из двух количеств — второе примется равным ему.">Количество по УПД/М-15<input type="number" min="0.000001" step="any" required={!fact} value={documentQty} onChange={(event) => setDocumentQty(event.target.value)} /></label>
              <label>Фактическое количество<input type="number" min="0.000001" step="any" required={!documentQty} value={fact} onChange={(event) => setFact(event.target.value)} /></label>
              <label>Срок годности<input type="date" value={expiry} onChange={(event) => setExpiry(event.target.value)} /></label>
              <label>Дата изготовления<input type="date" value={manufactureDate} onChange={(event) => setManufactureDate(event.target.value)} /></label>
              <label>Серия изготовителя<input value={series} onChange={(event) => setSeries(event.target.value)} /></label>
            </>}
            <label title="Вес ОДНОЙ штуки. Для крупного: разъём, катушка, корпус.">Масса 1 шт, г<input inputMode="decimal" value={mass.mass_1} onChange={(event) => setMass((current) => ({ ...current, mass_1: event.target.value }))} /></label>
            <label title="Вес ДЕСЯТИ штук — система поделит на 10. Клеммы, наконечники, крепёж.">Масса 10 шт, г<input inputMode="decimal" value={mass.mass_10} onChange={(event) => setMass((current) => ({ ...current, mass_10: event.target.value }))} /></label>
            <label title="Вес СТА штук — самый точный способ для мелочи. Хватит любого ОДНОГО из трёх полей.">Масса 100 шт, г<input inputMode="decimal" value={mass.mass_100} onChange={(event) => setMass((current) => ({ ...current, mass_100: event.target.value }))} /></label>
            <DocumentField lineId={line.id} kind="quality" label="Документ качества" value={qualityDocument} required={line.quality_document_required} disabled={disabled} onChange={setQualityDocument} />
            <label>Решение<select value={disposition} onChange={(event) => setDisposition(event.target.value)}><option value="">Ещё не принято</option><option>Изолятор</option><option>Использование по разрешению</option><option>Возврат поставщику</option></select></label>
            {!line.grn && (line.grn_prefix === null
              ? <label title="Назначение не распознано — ГРН получит выбранный идентификатор">Идентификатор ГРН *<select required value={prefix} onChange={(event) => setPrefix(event.target.value)}><option value="">— выберите —</option><option>CSM</option><option>PPM</option></select></label>
              : <label>Идентификатор ГРН<select value={prefix} onChange={(event) => setPrefix(event.target.value)}><option value="">{line.grn_prefix ? `Автоматически: ${line.grn_prefix}` : "Автоматически"}</option><option>CSM</option><option>PPM</option></select></label>)}
            <label className="check-field"><input type="checkbox" checked={historical} onChange={(event) => setHistorical(event.target.checked)} />Историческая приёмка: срок годности сверяется с датой УПД</label>
            <label className="wide">Примечание<input value={note} onChange={(event) => setNote(event.target.value)} /></label>
            <label className="wide">Описание несоответствия<input value={description} onChange={(event) => setDescription(event.target.value)} /></label>
            <fieldset className="check-field wide"><legend>Несоответствия ручной проверки</legend>{MANUAL_CHECKS.map((item) => <label key={item}><input type="checkbox" checked={manual.includes(item)} onChange={(event) => setManual((current) => event.target.checked ? [...current, item] : current.filter((value) => value !== item))} />{item}</label>)}</fieldset>
          </div>
          {batchMode && (
            <table className="batches">
              <thead><tr><th>Партия</th><th>По УПД</th><th>Факт</th><th>Серия</th><th>Срок годности</th><th>Дата изготовления</th><th /></tr></thead>
              <tbody>
                {batches.map((batch, index) => (
                  <tr key={index}>
                    <td>{index + 1}</td>
                    <td><input aria-label={`Партия ${index + 1}: по УПД`} type="number" min="0.000001" step="any" required={!batch.fact_quantity} value={batch.document_quantity} onChange={(event) => editBatch(index, { document_quantity: event.target.value })} /></td>
                    <td><input aria-label={`Партия ${index + 1}: факт`} type="number" min="0.000001" step="any" required={!batch.document_quantity} value={batch.fact_quantity} onChange={(event) => editBatch(index, { fact_quantity: event.target.value })} /></td>
                    <td><input aria-label={`Партия ${index + 1}: серия`} value={batch.series} onChange={(event) => editBatch(index, { series: event.target.value })} /></td>
                    <td><input aria-label={`Партия ${index + 1}: срок годности`} type="date" value={batch.expiry_date} onChange={(event) => editBatch(index, { expiry_date: event.target.value })} /></td>
                    <td><input aria-label={`Партия ${index + 1}: дата изготовления`} type="date" value={batch.manufacture_date} onChange={(event) => editBatch(index, { manufacture_date: event.target.value })} /></td>
                    <td><button type="button" className="secondary small" onClick={() => setBatches((current) => current.length > 2 ? current.filter((_, position) => position !== index) : [])}>✕</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="header-actions">
            {!line.grn && <button type="button" className="secondary small" disabled={disabled} onClick={startBatches}>+ партия</button>}
            <button className="primary small" disabled={disabled}>{busy ? "Проводим…" : batchMode ? `Провести ВК по партиям (${batches.length})` : "Провести входной контроль"}</button>
          </div>
        </form>
      )}
      {placeable && (
        <form className="inline-action" onSubmit={(event) => { event.preventDefault(); void onPlace(line, location).catch(() => undefined); }}>
          <label>Место на складе<input required placeholder="1F4" title={placeHint.busy ? BUSY_TITLE : undefined} value={location} onChange={(event) => setLocation(event.target.value)} /></label>
          <button className="primary small" disabled={disabled}>{busy ? "Передаём…" : "Передать на склад"}</button>
          <PlaceHint {...placeHint} place={location} onPick={setLocation} />
        </form>
      )}
      {unpackable && (packages.length ? (
        <form className="inline-action" onSubmit={(event) => { event.preventDefault(); void onUnpack(line, packages).then(() => setPackages([])).catch(() => undefined); }}>
          {packages.map((value, index) => (
            <label key={index}>Пакет {index + 1}<input aria-label={`Пакет ${index + 1}`} type="number" min="0.000001" step="any" required value={value} onChange={(event) => setPackages((current) => current.map((item, position) => position === index ? event.target.value : item))} /></label>
          ))}
          <button type="button" className="secondary small" onClick={() => setPackages((current) => [...current, ""])}>+ пакет</button>
          <button className="primary small" disabled={disabled}>{busy ? "Делим…" : `Разукомплектовать (сумма = ${line.fact_quantity})`}</button>
          <button type="button" className="secondary small" onClick={() => setPackages([])}>Отмена</button>
        </form>
      ) : (
        <button className="secondary small" disabled={disabled} onClick={() => setPackages([line.fact_quantity ?? "", ""])}>Разукомплектовать на пакеты</button>
      ))}
      {revisable && (
        <button className="secondary small" disabled={disabled} onClick={() => void onOpenVkRevision(line)}>Провести новый ВК</button>
      )}
      {Boolean(line.acts?.length) && (
        <div className="header-actions acts">
          {line.acts!.map((act) => (
            <button key={act.kind} type="button" className="secondary small" onClick={() => void receivingApi.actSheet(line.id, act.kind).then(({ sheet }) => openPrintSheet(sheet)).catch(() => undefined)}>
              {act.title} № {act.number}
            </button>
          ))}
        </div>
      )}
      {line.status === "Архив ВК" && <p className="hint">Архив ВК: акты этой строки — история, приёмка идёт по ревизии.</p>}
      {line.warehouse_event_emitted && <div className="success-banner">Передано на склад: {line.grn} · {line.storage_location}</div>}
    </article>
  );
}

interface Batch {
  document_quantity: string;
  fact_quantity: string;
  series: string;
  expiry_date: string;
  manufacture_date: string;
}

function emptyBatch(): Batch {
  return { document_quantity: "", fact_quantity: "", series: "", expiry_date: "", manufacture_date: "" };
}
