import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { AutoTextarea } from "./AutoTextarea";
import { ApiError, failureText, receivingApi, type DeliveryDeviation, type DeliveryLinePayload, type ReceivingLine, type ReceivingQuery } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate } from "./format";
import { StatusChip } from "./StatusChip";
import { openPrintSheet } from "./print";
import { useStageOption } from "./StageOptions";
import { projectLabel, useInternalProjects } from "./internalProjects";
import { usePurposeLabel } from "./purposeLabels";
import { VIA_LABELS, purchasingItemsApi, type ItemBrief } from "./purchasingItemsApi";
import { SupplierReturnPanel } from "./SupplierReturnPanel";
import { BUSY_TITLE, PlaceHint, usePlaceHint } from "./WarehousePlaceHint";

const MANUAL_CHECKS = [
  "Механическое повреждение / брак",
  "Не тот товар (несоответствие счету)",
];

const COLUMNS: DataColumn<ReceivingLine>[] = [
  { key: "legacy_id", title: "ID", value: (line) => line.legacy_id, hidden: true },
  { key: "name", title: "Наименование", value: (line) => itemTitle(line), wrap: true, grow: true, minWidth: 200 },
  { key: "supplier", title: "Поставщик", value: (line) => line.supplier, filter: true },
  { key: "order", title: "№ заказа", value: (line) => line.order_number ?? "" },
  { key: "expected", title: "Ожид. дата", value: (line) => line.expected_arrival ?? "" },
  { key: "purpose", title: "Назначение", value: (line) => line.purpose, filter: true, wrap: true, width: 140, hidden: true },
  { key: "goal", title: "Проект", value: (line) => projectLabel(line.goal), filter: true, wrap: true, width: 140, hidden: true },
  { key: "ordered", title: "Заказано", value: (line) => line.ordered_quantity, numeric: true, render: (line) => `${line.ordered_quantity} ${line.unit}` },
  { key: "status", title: "Статус", value: (line) => line.status, filter: true, render: (line) => <StatusChip status={line.status} /> },
  { key: "document", title: "УПД", value: (line) => [line.document_number, formatDate(line.document_date)].filter(Boolean).join(" от ") || (line.shipping_document_note ? "ждём по ЭДО" : ""), wrap: true, width: 100 },
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

/** Быстрый отбор очереди — на сервере (`preset` / `status`), чтобы отбор работал дальше первой страницы. */
const QUEUE_CHIPS: { label: string; query: ReceivingQuery }[] = [
  { label: "В пути", query: { status: ["В пути"] } },
  { label: "Ожидание", query: { status: ["Ожидание"] } },
  { label: "Карантин", query: { status: ["Карантин"] } },
  { label: "Принято, не разложено", query: { preset: "to_stock" } },
  { label: "На складе", query: { preset: "done" } },
  { label: "Возврат", query: { status: ["Возврат"] } },
  { label: "Архив ВК", query: { preset: "archive" } },
];

/** Эти очереди целиком входят в «открытое» (`waiting`), по ним счётчик на чипе известен без лишнего запроса. */
const COUNTED_CHIPS = new Set(["В пути", "Ожидание", "Карантин"]);

/** «Внести УПД»: all but «Архив ВК» and «Возврат» — УПД по ЭДО приходит и после приёмки. */
const UPD_STATUSES = new Set(["В пути", "Ожидание", "Карантин", "Принято"]);

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

interface ExpectedGroup {
  key: string;
  supplier: string;
  warehouse: string;
  orders: string[];
  lines: ReceivingLine[];
  from: string | null;
  to: string | null;
  /** Склад показывается, только когда у поставщика позиции на двух складах. */
  showWarehouse: boolean;
}

/** «Ожидаются»: открытые «В пути» / «Ожидание», по поставщику (и складу — одна поставка идёт на один склад),
 *  ближайшая дата ожидания первой. */
export function expectedGroups(lines: ReceivingLine[]): ExpectedGroup[] {
  const groups = new Map<string, ExpectedGroup>();
  for (const line of lines) {
    if (line.status !== "В пути" && line.status !== "Ожидание") continue;
    const supplier = line.supplier || "Без поставщика";
    const key = `${supplier}\u0000${line.warehouse}`;
    const group = groups.get(key) ?? { key, supplier, warehouse: line.warehouse, orders: [], lines: [], from: null, to: null, showWarehouse: false };
    group.lines.push(line);
    if (line.order_number && !group.orders.includes(line.order_number)) group.orders.push(line.order_number);
    if (line.expected_arrival) {
      if (!group.from || line.expected_arrival < group.from) group.from = line.expected_arrival;
      if (!group.to || line.expected_arrival > group.to) group.to = line.expected_arrival;
    }
    groups.set(key, group);
  }
  const result = [...groups.values()];
  for (const group of result) {
    group.orders.sort();
    group.showWarehouse = result.filter((other) => other.supplier === group.supplier).length > 1;
  }
  return result.sort((a, b) => (a.from ?? "9999").localeCompare(b.from ?? "9999") || a.supplier.localeCompare(b.supplier, "ru"));
}

function expectedText(group: ExpectedGroup) {
  if (!group.from) return "срок не указан";
  return group.to && group.to !== group.from ? `ждём ${formatDate(group.from)} – ${formatDate(group.to)}` : `ждём ${formatDate(group.from)}`;
}

function ExpectedBlock({ groups, readOnly, loading, onAccept }: {
  groups: ExpectedGroup[];
  readOnly: boolean;
  loading: boolean;
  onAccept: (group: ExpectedGroup) => void;
}) {
  if (!groups.length) return null;
  return (
    <section className="panel expected-block" aria-label="Ожидаются">
      <div className="upd-head"><h2>Ожидаются</h2><span className="upd-ids">{groups.length} {groups.length === 1 ? "поставщик" : "поставщиков"}</span></div>
      <ul className="expected-list">
        {groups.map((group) => (
          <li key={group.key}>
            <span className="expected-text">
              <strong>{group.supplier}</strong>{group.showWarehouse ? ` (${group.warehouse})` : ""}
              {group.orders.length > 0 && <> · {group.orders.join(", ")}</>}
              {" · "}{group.lines.length} поз. · {expectedText(group)}
            </span>
            {!readOnly && <button type="button" className="primary small" disabled={loading} onClick={() => onAccept(group)}>Принять поставку</button>}
          </li>
        ))}
      </ul>
    </section>
  );
}

interface DeliveryRow {
  line: ReceivingLine;
  ticked: boolean;
  document: string;
  fact: string;
  place: string;
  deviation: DeliveryDeviation;
  deviationQuantity: string;
  expiry: string;
}

const DEVIATIONS: { value: DeliveryDeviation; label: string }[] = [
  { value: "none", label: "—" },
  { value: "brak", label: "Брак" },
  { value: "peresort", label: "Пересорт" },
  { value: "no_quality_doc", label: "Нет документа качества" },
  { value: "expired", label: "Истёк срок" },
];

/** «Принять поставку»: все позиции поставщика отмечены, количества — только при расхождении, УПД — по желанию. */
function DeliveryPanel({ group, onClose, onDone }: {
  group: ExpectedGroup;
  onClose: () => void;
  onDone: (lines: ReceivingLine[]) => void;
}) {
  const [rows, setRows] = useState<DeliveryRow[]>(() => group.lines.map((line) => ({
    line, ticked: true, document: "", fact: "", place: line.storage_location ?? "", deviation: "none", deviationQuantity: "", expiry: "",
  })));
  const [url, setUrl] = useState("");
  const [number, setNumber] = useState("");
  const [date, setDate] = useState("");
  const [commonPlace, setCommonPlace] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState<Map<string, ReceivingLine> | null>(null);
  const placeHint = usePlaceHint(group.warehouse, commonPlace, rows.map((row) => row.place).filter(Boolean));
  const chosen = rows.filter((row) => row.ticked);
  const done = results !== null;

  function edit(id: string, patch: Partial<DeliveryRow>) {
    setRows((current) => current.map((row) => row.line.id === id ? { ...row, ...patch } : row));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || done || !chosen.length) return;
    setBusy(true);
    setError("");
    try {
      const lines: DeliveryLinePayload[] = chosen.map((row) => {
        const place = row.place.trim() || commonPlace.trim();
        return {
          id: row.line.id,
          version: row.line.version,
          ...(row.document.trim() ? { document_quantity: row.document.trim() } : {}),
          ...(row.fact.trim() ? { fact_quantity: row.fact.trim() } : {}),
          ...(place ? { storage_location: place } : {}),
          ...(row.deviation !== "none" ? { deviation: row.deviation } : {}),
          ...(row.deviation === "brak" && row.deviationQuantity.trim() ? { deviation_quantity: row.deviationQuantity.trim() } : {}),
          ...(row.deviation === "expired" && row.expiry ? { expiry_date: row.expiry } : {}),
        };
      });
      const result = await receivingApi.acceptDelivery({
        lines,
        shipping_document_url: url.trim() || null,
        document_number: number.trim(),
        document_date: date || null,
        warehouse: group.warehouse,
      });
      setResults(new Map(result.lines.map((line) => [line.id, line])));
      onDone(result.lines);
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 409
        ? "Строки изменились — обновите список"
        : failureText(reason, "Поставка не проведена"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel delivery-panel" onSubmit={(event) => void submit(event)}>
      <div className="upd-head">
        <h2>Поставка: {group.supplier}{group.showWarehouse ? ` (${group.warehouse})` : ""}</h2>
        <span className="upd-ids">{group.warehouse} · {expectedText(group)}</span>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="upd-row delivery-head">
        <DocumentField lineId={group.lines[0].id} kind="upd" label="Документ УПД" value={url} disabled={busy || done} inline onChange={setUrl} />
        <label>УПД №<input value={number} disabled={busy || done} onChange={(event) => setNumber(event.target.value)} /></label>
        <label>Дата УПД<input type="date" value={date} disabled={busy || done} onChange={(event) => setDate(event.target.value)} /></label>
        <label>Место для всех<input placeholder="1F4" title={placeHint.busy ? BUSY_TITLE : undefined} value={commonPlace} disabled={busy || done} onChange={(event) => setCommonPlace(event.target.value)} /></label>
      </div>
      <p className="page-sub">УПД по ЭДО можно внести позже — «Внести УПД».</p>
      {/* После проведения место уже занято этой же поставкой — подсказка «здесь лежит другая позиция» ложная. */}
      {!done && <PlaceHint {...placeHint} place={commonPlace} onPick={setCommonPlace} />}
      <div className="table-wrap">
        <table className="delivery-table">
          <thead><tr><th /><th>Наименование</th><th>Заказ</th><th>Ждём</th><th>По УПД</th><th>Факт</th><th>Место</th><th>Отклонение</th>{done && <th>ГРН</th>}</tr></thead>
          <tbody>
            {rows.map((row) => {
              const result = results?.get(row.line.id);
              const locked = busy || done || !row.ticked;
              return (
                <tr key={row.line.id} className={row.ticked ? "" : "off"}>
                  <td><input type="checkbox" aria-label={`Пришло: ${itemTitle(row.line)}`} checked={row.ticked} disabled={busy || done} onChange={(event) => edit(row.line.id, { ticked: event.target.checked })} /></td>
                  <td className="name">{itemTitle(row.line)}</td>
                  <td>{row.line.order_number ?? ""}</td>
                  <td className="num">{row.line.ordered_quantity} {row.line.unit}</td>
                  <td><input type="number" min="0.000001" step="any" className="qty" placeholder="как заказано" aria-label={`По УПД: ${itemTitle(row.line)}`} value={row.document} disabled={locked} onChange={(event) => edit(row.line.id, { document: event.target.value })} /></td>
                  <td><input type="number" min="0.000001" step="any" className="qty" placeholder="как по УПД" aria-label={`Факт: ${itemTitle(row.line)}`} value={row.fact} disabled={locked} onChange={(event) => edit(row.line.id, { fact: event.target.value })} /></td>
                  <td><input className="place" placeholder={commonPlace || "1F4"} aria-label={`Место: ${itemTitle(row.line)}`} value={row.place} disabled={locked} onChange={(event) => edit(row.line.id, { place: event.target.value })} /></td>
                  <td className="deviation">
                    <select aria-label={`Отклонение: ${itemTitle(row.line)}`} value={row.deviation} disabled={locked} onChange={(event) => edit(row.line.id, { deviation: event.target.value as DeliveryDeviation })}>
                      {DEVIATIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                    {row.deviation === "brak" && <input type="number" min="0.000001" step="any" className="qty" placeholder={`N ${row.line.unit}`} aria-label={`Брак, ${row.line.unit}: ${itemTitle(row.line)}`} value={row.deviationQuantity} disabled={locked} onChange={(event) => edit(row.line.id, { deviationQuantity: event.target.value })} />}
                    {row.deviation === "expired" && <input type="date" aria-label={`Срок годности: ${itemTitle(row.line)}`} value={row.expiry} disabled={locked} onChange={(event) => edit(row.line.id, { expiry: event.target.value })} />}
                  </td>
                  {done && <td className="num">{row.ticked ? (result?.grn ? <strong>{result.grn}</strong> : <StatusChip status={result?.status} />) : ""}{result && Number(result.backorder_quantity) > 0 && <span className="delivery-short">недовоз {result.backorder_quantity} {result.unit} → дозавоз</span>}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="header-actions">
        {!done && <button type="submit" className="primary small" disabled={busy || !chosen.length}>{busy ? "Проводим…" : `Провести поставку (${chosen.length} поз.)`}</button>}
        <button type="button" className="secondary small" disabled={busy} onClick={onClose}>{done ? "Закрыть" : "Отмена"}</button>
      </div>
    </form>
  );
}

/** «Принято N позиций, ГРН присвоены; недовоз → дозавоз: …»; не принятые (брак, возврат) называются отдельно;
 *  без УПД — пометка «УПД не приложен — придёт по ЭДО». */
export function deliveryDoneText(lines: ReceivingLine[]) {
  const accepted = lines.filter((line) => line.status === "Принято");
  const rest = lines.filter((line) => line.status !== "Принято");
  const backorders = lines.filter((line) => Number(line.backorder_quantity) > 0);
  const parts = [`Принято ${pluralLines(accepted.length)}, ГРН присвоены`];
  if (backorders.length) parts.push(`недовоз → дозавоз: ${backorders.map((line) => `${itemTitle(line)} ${line.backorder_quantity} ${line.unit}`).join(", ")}`);
  if (rest.length) parts.push(`не приняты: ${rest.map((line) => `${itemTitle(line)} — ${line.status}`).join(", ")}`);
  // Тот же вычисляемый `shipping_document_note`, что у одиночного ВК без УПД.
  const pending = lines.find((line) => line.shipping_document_note)?.shipping_document_note;
  if (pending) parts.push(`${pending} (внесите кнопкой «Внести УПД»)`);
  return parts.join("; ");
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
  const itemsOn = useStageOption("references.items");
  const [lineItems, setLineItems] = useState<Record<string, ItemBrief>>({});
  const [group, setGroup] = useState("order");
  const [updRows, setUpdRows] = useState<ReceivingLine[] | null>(null);
  const [notice, setNotice] = useState("");
  const [chip, setChip] = useState("");
  const [waiting, setWaiting] = useState<ReceivingLine[]>([]);
  const [delivery, setDelivery] = useState<ExpectedGroup | null>(null);
  const chipRef = useRef("");
  chipRef.current = chip;

  useEffect(() => {
    // Option off: no request. A failed read just leaves the lines without the item name.
    if (!itemsOn || lines.length === 0) { setLineItems({}); return; }
    let live = true;
    const ids = lines.map((line) => line.id);
    const parts: string[][] = [];
    for (let start = 0; start < ids.length; start += 500) parts.push(ids.slice(start, start + 500));
    Promise.all(parts.map((part) => purchasingItemsApi.forReceiving(part)))
      .then((answers) => { if (live) setLineItems(Object.assign({}, ...answers)); })
      .catch(() => { if (live) setLineItems({}); });
    return () => { live = false; };
  }, [itemsOn, lines]);
  const purposeText = usePurposeLabel();
  useInternalProjects(); // option projects.internal: the «Проект» column shows «ВП-001 · Название»
  const columns = useMemo(() => COLUMNS.map((column) => column.key === "purpose" ? { ...column, value: (line: ReceivingLine) => purposeText(line.purpose) } : column).map((column) => !itemsOn || column.key !== "name" ? column : {
    ...column,
    render: (line: ReceivingLine) => {
      const brief = lineItems[line.id];
      return <>{itemTitle(line)}{brief && <small className="muted lot-item-chip" title={`Позиция номенклатуры, связь: ${VIA_LABELS[brief.via]}`}>{brief.name}</small>}</>;
    },
  }), [itemsOn, lineItems, purposeText]);
  const requestSeq = useRef(0);

  function queueQuery(after?: string): ReceivingQuery {
    const chosen = QUEUE_CHIPS.find((item) => item.label === chipRef.current);
    return { ...(chosen?.query ?? {}), ...(after ? { after } : {}) };
  }

  /** «Ожидаются» не зависит от отбора очереди: всё открытое, до 10 страниц по 200. */
  async function loadWaiting() {
    try {
      const collected: ReceivingLine[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < 10; page += 1) {
        const result = await receivingApi.list({ preset: "open", limit: 200, after: cursor });
        collected.push(...result.lines);
        if (!result.next_cursor) break;
        cursor = result.next_cursor;
      }
      setWaiting(collected);
    } catch {
      /* «Ожидаются» — подсказка над очередью: ошибка очереди ниже остаётся единственной */
    }
  }

  /** Очередь и «Ожидаются» заново — после любого действия. */
  async function refreshAll() {
    const result = await receivingApi.list(queueQuery());
    setLines(result.lines);
    setNextCursor(result.next_cursor);
    void loadWaiting();
  }

  async function reload(after?: string) {
    if (after && (loading || busy)) return;
    const seq = ++requestSeq.current;
    setLoading(true);
    setError("");
    try {
      const result = await receivingApi.list(queueQuery(after));
      if (seq !== requestSeq.current) return;
      setLines((current) => after
        ? [...new Map([...current, ...result.lines].map((line) => [line.id, line])).values()]
        : result.lines);
      setNextCursor(result.next_cursor);
      if (!after) void loadWaiting();
    } catch (reason) {
      if (seq === requestSeq.current) setError(reason instanceof Error ? reason.message : "Не удалось загрузить приёмку");
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }

  useEffect(() => { void reload(); }, [chip]);

  async function inspect(line: ReceivingLine, payload: Record<string, unknown>) {
    if (busy || loading) return;
    setBusy(line.id);
    setError("");
    try {
      const done = await receivingApi.inspect(line.id, { version: line.version, ...payload });
      const assigned = (done.lines ?? [done.line]).filter((item) => item.grn);
      if (assigned.length) setNotice(`Присвоены ГРН: ${assigned.map((item) => `${item.legacy_id} — ${item.grn}`).join("; ")}`);
      await refreshAll();
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
      await refreshAll();
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
      await refreshAll();
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
      await refreshAll();
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
      await refreshAll();
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
      <ExpectedBlock groups={expectedGroups(waiting)} readOnly={readOnly} loading={loading || Boolean(busy)} onAccept={(group) => { setNotice(""); setUpdRows(null); setDelivery(group); }} />
      {delivery && !readOnly && <DeliveryPanel key={delivery.key} group={delivery} onClose={() => setDelivery(null)} onDone={(done) => { setNotice(deliveryDoneText(done)); void refreshAll().catch(() => undefined); }} />}
      {updRows && <UpdPanel rows={updRows} busy={Boolean(busy)} onSubmit={(payload) => void attachDocuments(updRows, payload)} onClose={() => setUpdRows(null)} />}
      {notice && <div className="success-banner" role="status">{notice}</div>}
      <div className="calendar-chips queue-chips" role="group" aria-label="Быстрый отбор">
        <button type="button" className={`calendar-chip${chip ? "" : " active"}`} aria-pressed={!chip} onClick={() => setChip("")}>Все</button>
        {QUEUE_CHIPS.map((item) => (
          <button type="button" key={item.label} className={`calendar-chip${chip === item.label ? " active" : ""}`} aria-pressed={chip === item.label} onClick={() => setChip(chip === item.label ? "" : item.label)}>{item.label}{COUNTED_CHIPS.has(item.label) ? ` · ${waiting.filter((line) => line.status === item.label).length}` : ""}</button>
        ))}
      </div>
      <DataTable
        rows={lines}
        columns={columns}
        rowKey={(line) => line.id}
        storageKey="receiving-v3"
        label="Очередь приёмки"
        groupBy={GROUPS[group]}
        groupLabel={group === "order" ? orderGroupLabel : undefined}
        exportName="Приёмка"
        emptyText={loading ? "Загружаем…" : "Поставок для приёмки пока нет."}
        bulkActions={[
          ...(readOnly ? [] : [{
            label: "Внести УПД",
            run: async (rows: ReceivingLine[]) => { setNotice(""); setUpdRows(rows); },
            disabled: (rows: ReceivingLine[]) => rows.some((line) => !UPD_STATUSES.has(line.status)),
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
        <StatusChip status={line.status} />
      </div>
      {line.nonconformities.length > 0 && <p className="nonconformity">{line.nonconformities.join(" · ")}</p>}
      {line.nonconformity_description && <p className="nonconformity">{line.nonconformity_description}</p>}
      {line.shipping_document_note && <p className="hint">{line.shipping_document_note}</p>}
      {(line.document_number || line.inspection_note) && <p className="hint">{[line.document_number && `УПД ${line.document_number}${line.document_date ? ` от ${line.document_date}` : ""}`, line.inspection_note].filter(Boolean).join(" · ")}</p>}
      {Number(line.backorder_quantity) > 0 && <p className="nonconformity">Недовоз {line.backorder_quantity} {line.unit} — оплаченный остаток ушёл в закупку дозавозом</p>}
      {inspectable && (
        <form onSubmit={submitInspection}>
          <div className="facts-grid">
            <DocumentField lineId={line.id} kind="upd" label="Документ УПД/М-15" value={shippingDocument} disabled={disabled} hint="УПД по ЭДО можно внести позже — «Внести УПД» в списке." onChange={setShippingDocument} />
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
            <label className="wide">Примечание<AutoTextarea value={note} onChange={setNote} /></label>
            <label className="wide">Описание несоответствия<AutoTextarea value={description} onChange={setDescription} /></label>
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
