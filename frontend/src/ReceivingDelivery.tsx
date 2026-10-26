import { useRef, useState, type FormEvent } from "react";
import {
  ApiError, failureText, receivingApi,
  type DeliveryDeviation, type DeliveryLinePayload, type ReceivingLine, type UpdProposal, type WarehouseReceiptScan,
} from "./api";
import { formatDate } from "./format";
import { StatusChip } from "./StatusChip";
import { ReceiptLabels, type ReceiptLot } from "./LabelPrint";
import { BUSY_TITLE, PlaceHint, usePlaceHint } from "./WarehousePlaceHint";
import { PageWindow } from "./PageWindow";
import { useUpdScan } from "./useUpdScan";
import {
  CONFIDENCE, initialRows, reassignRow, rowTargets, rowTitle, type UpdShare,
} from "./receivingByUpdLogic";

/** Артикул · наименование; without a design article the order carries the name in both — show it once. */
export function itemTitle(line: ReceivingLine) {
  return [...new Set([line.article, line.name].map((value) => (value ?? "").trim()).filter(Boolean))].join(" · ");
}

/** The GRN lots a receipt has put on stock — for «Печать этикеток». */
export function receiptLots(lines: (ReceivingLine | undefined)[]): ReceiptLot[] {
  return lines.filter((line): line is ReceivingLine => Boolean(line?.grn && line.warehouse_event_emitted)).map((line) => ({
    key: line.id, warehouse: line.warehouse, grn: line.grn!, package: line.package, title: itemTitle(line),
    quantity: `${line.fact_quantity ?? line.ordered_quantity} ${line.unit}`.trim(),
  }));
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
export function DocumentField({ lineId, kind, label, value, required, disabled, hint, inline, onChange }: {
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

export const pluralLines = (count: number) => `${count} ${count % 10 === 1 && count % 100 !== 11 ? "позиция" : count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14) ? "позиции" : "позиций"}`;

export interface ExpectedGroup {
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

export function expectedText(group: ExpectedGroup) {
  if (!group.from) return "срок не указан";
  return group.to && group.to !== group.from ? `ждём ${formatDate(group.from)} – ${formatDate(group.to)}` : `ждём ${formatDate(group.from)}`;
}

/** The delivery a recognised УПД is: its lines (matched first) as one group of its supplier and warehouse. */
export function proposalGroup(proposal: UpdProposal): ExpectedGroup {
  const lines = proposal.lines.map((item) => item.line);
  const days = lines.map((line) => line.expected_arrival).filter((day): day is string => Boolean(day)).sort();
  const supplier = proposal.supplier || lines[0]?.supplier || "Без поставщика";
  return {
    key: `upd\u0000${supplier}\u0000${proposal.warehouse}`, supplier, warehouse: proposal.warehouse, lines, showWarehouse: false,
    orders: [...new Set(lines.map((line) => line.order_number).filter((order): order is string => Boolean(order)))].sort(),
    from: days[0] ?? null, to: days[days.length - 1] ?? null,
  };
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
  /** «Принять по УПД»: the УПД rows this line takes, how it was found, what to check */
  shares?: UpdShare[];
  confidence?: string;
  warnings?: string[];
}

/** «По УПД» меньше заказанного — сколько уйдёт дозавозом («Недовоз»); "" — не меньше или не введено. */
function shortOf(row: DeliveryRow) {
  const document = Number(row.document.trim().replace(",", "."));
  const ordered = Number(row.line.ordered_quantity);
  if (!row.document.trim() || !Number.isFinite(document) || !(document < ordered)) return "";
  return String(Math.round((ordered - document) * 1_000_000) / 1_000_000);
}

/** Факт строки поставки, как его примет сервер: введённый, иначе по УПД, иначе заказанное. */
function deliveryFact(row: DeliveryRow) {
  return row.fact.trim() || row.document.trim() || row.line.ordered_quantity;
}

const DEVIATIONS: { value: DeliveryDeviation; label: string }[] = [
  { value: "none", label: "—" },
  { value: "brak", label: "Брак" },
  { value: "peresort", label: "Пересорт" },
  { value: "no_quality_doc", label: "Нет документа качества" },
  { value: "expired", label: "Истёк срок" },
];

/** A recognised УПД the delivery is pre-filled from. */
export interface UpdDelivery {
  scan: WarehouseReceiptScan;
  proposal: UpdProposal;
  pages: number;
  edo: boolean;
}

const blankRow = (line: ReceivingLine): DeliveryRow => ({
  line, ticked: true, document: "", fact: "", place: line.storage_location ?? "", deviation: "none", deviationQuantity: "", expiry: "",
});

/** «Принять поставку»: все позиции поставщика отмечены, количества — только при расхождении, УПД — по желанию.
 *  По УПД (`upd`): отмечены найденные строки с «По УПД» по раскладке, скан — документ поставки, строки УПД без пары
 *  — отдельно, любую строку УПД можно перенести на другую позицию. */
export function DeliveryPanel({ group, upd, onClose, onDone, onWarehouse }: {
  group: ExpectedGroup;
  upd?: UpdDelivery;
  onClose: () => void;
  onDone: (lines: ReceivingLine[]) => void;
  /** «Принять по УПД»: строки этого УПД ждут и на другом складе — открыть ту поставку */
  onWarehouse?: (warehouse: string) => void;
}) {
  const [rows, setRows] = useState<DeliveryRow[]>(() => upd
    ? initialRows(upd.proposal).map((item) => ({ ...blankRow(item.line), ...item }))
    : group.lines.map(blankRow));
  const [url, setUrl] = useState("");
  const [number, setNumber] = useState(upd?.scan.recognised.upd_number ?? "");
  const [date, setDate] = useState((upd?.scan.recognised.upd_date ?? "").slice(0, 10));
  const [commonPlace, setCommonPlace] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState<Map<string, ReceivingLine> | null>(null);
  const placeHint = usePlaceHint(group.warehouse, commonPlace, rows.map((row) => row.place).filter(Boolean));
  const chosen = rows.filter((row) => row.ticked);
  const done = results !== null;
  const updRows = upd?.proposal.rows ?? [];
  const unmatched = updRows.filter((row) => rowTargets(rows, row.index).length === 0);

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
      const payload = {
        lines,
        shipping_document_url: url.trim() || null,
        document_number: number.trim(),
        document_date: date || null,
        warehouse: group.warehouse,
      };
      const result = upd
        ? await receivingApi.acceptByUpd({ ...payload, scan_file: upd.scan.scan_file, recognised: upd.scan.recognised })
        : await receivingApi.acceptDelivery(payload);
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

  const recognised = upd?.scan.recognised;
  const invoice = recognised?.invoice_number ? `счёт ${recognised.invoice_number}${recognised.invoice_date ? ` от ${formatDate(recognised.invoice_date)}` : ""}` : "";
  return (
    <form className="panel delivery-panel" onSubmit={(event) => void submit(event)}>
      <div className="upd-head">
        <h2>Поставка: {group.supplier}{group.showWarehouse ? ` (${group.warehouse})` : ""}</h2>
        <span className="upd-ids">{group.warehouse} · {upd ? [invoice, upd.proposal.orders.length ? `заказы ${upd.proposal.orders.join(", ")}` : ""].filter(Boolean).join(" · ") || "по поставщику" : expectedText(group)}</span>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {upd && <p className="upd-match-note">
        {upd.edo ? "Файл ЭДО" : `Скан УПД${upd.pages > 1 ? ` · ${upd.pages} стр.` : ""}`}: <a href={upd.scan.scan_file} target="_blank" rel="noreferrer">открыть</a>
        {" · "}найдено в ожидании: <b>{rows.filter((row) => row.shares?.length).length}</b> из {updRows.length} строк УПД
        {unmatched.length > 0 && <> · <span className="tone-warn">без пары: {unmatched.length}</span></>}
      </p>}
      {upd && upd.proposal.other_warehouses.map((other) => (
        <p key={other.warehouse} className="warning-inline">
          Ещё {other.matched} поз. этого УПД ждут на складе {other.warehouse}.{" "}
          {onWarehouse && <button type="button" className="link-button" disabled={busy || done} onClick={() => onWarehouse(other.warehouse)}>Открыть поставку на {other.warehouse}</button>}
        </p>
      ))}
      <div className="upd-row delivery-head">
        {upd ? <label>Поставщик по УПД<input value={recognised?.supplier || "не распознан"} readOnly /></label>
          : <DocumentField lineId={group.lines[0].id} kind="upd" label="Документ УПД" value={url} disabled={busy || done} inline onChange={setUrl} />}
        <label>УПД №<input value={number} disabled={busy || done} onChange={(event) => setNumber(event.target.value)} /></label>
        <label>Дата УПД<input type="date" value={date} disabled={busy || done} onChange={(event) => setDate(event.target.value)} /></label>
        <label>Место для всех<input placeholder="1F4" title={placeHint.busy ? BUSY_TITLE : undefined} value={commonPlace} disabled={busy || done} onChange={(event) => setCommonPlace(event.target.value)} /></label>
      </div>
      {!upd && <p className="page-sub">УПД по ЭДО можно внести позже — «Внести УПД».</p>}
      {/* После проведения место уже занято этой же поставкой — подсказка «здесь лежит другая позиция» ложная. */}
      {!done && <PlaceHint {...placeHint} place={commonPlace} onPick={setCommonPlace} />}
      <div className="table-wrap">
        <table className="delivery-table">
          <thead><tr><th /><th>Наименование</th><th>Заказ</th><th>Ждём</th>{upd && <th>По УПД нашли</th>}<th>По УПД</th><th>Факт</th><th>Место</th><th>Отклонение</th>{done && <th>ГРН</th>}</tr></thead>
          <tbody>
            {rows.map((row) => {
              const result = results?.get(row.line.id);
              const locked = busy || done || !row.ticked;
              const confidence = row.confidence ? CONFIDENCE[row.confidence as keyof typeof CONFIDENCE] : undefined;
              return (
                <tr key={row.line.id} className={row.ticked ? "" : "off"}>
                  <td><input type="checkbox" aria-label={`Пришло: ${itemTitle(row.line)}`} checked={row.ticked} disabled={busy || done} onChange={(event) => edit(row.line.id, { ticked: event.target.checked })} /></td>
                  <td className="name">{itemTitle(row.line)}</td>
                  <td>{row.line.order_number ?? ""}</td>
                  <td className="num">{row.line.ordered_quantity} {row.line.unit}</td>
                  {upd && <td className="upd-found">
                    {confidence ? <StatusChip tone={confidence.tone} title={confidence.title}>{confidence.label}</StatusChip> : <span className="muted">нет в УПД</span>}
                    {(row.shares ?? []).length > 0 && <small className="muted"> строка УПД {(row.shares ?? []).map((share) => share.index + 1).join(", ")}</small>}
                    {(row.warnings ?? []).map((text) => <small key={text} className="tone-warn upd-warning">{text}</small>)}
                    {!done && row.ticked && shortOf(row) &&<small className="tone-warn upd-warning">недовоз {shortOf(row)} {row.line.unit} → дозавоз</small>}
                  </td>}
                  <td><input type="number" min="0.000001" step="any" className="qty" placeholder="как заказано" aria-label={`По УПД: ${itemTitle(row.line)}`} value={row.document} disabled={locked} onChange={(event) => edit(row.line.id, { document: event.target.value })} /></td>
                  <td><input type="number" min="0.000001" step="any" className="qty" placeholder="как по УПД" aria-label={`Факт: ${itemTitle(row.line)}`} value={row.fact} disabled={locked} onChange={(event) => edit(row.line.id, { fact: event.target.value })} /></td>
                  <td><input className="place" placeholder={commonPlace || "1F4"} aria-label={`Место: ${itemTitle(row.line)}`} value={row.place} disabled={locked} onChange={(event) => edit(row.line.id, { place: event.target.value })} /></td>
                  <td className="deviation">
                    <select aria-label={`Отклонение: ${itemTitle(row.line)}`} value={row.deviation} disabled={locked} onChange={(event) => {
                      const deviation = event.target.value as DeliveryDeviation;
                      // Брак — по умолчанию вся строка (факт); приёмщик уменьшает до числа бракованных.
                      edit(row.line.id, deviation === "brak" && !row.deviationQuantity.trim() ? { deviation, deviationQuantity: deliveryFact(row) } : { deviation });
                    }}>
                      {DEVIATIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                    {row.deviation === "brak" && <label className="defect-qty">Кол-во брака<input type="number" min="0.000001" max={deliveryFact(row)} step="any" required className="qty" title={`Не больше факта: ${deliveryFact(row)} ${row.line.unit}`} aria-label={`Кол-во брака, ${row.line.unit}: ${itemTitle(row.line)}`} value={row.deviationQuantity} disabled={locked} onChange={(event) => edit(row.line.id, { deviationQuantity: event.target.value })} /><span>{row.line.unit}</span></label>}
                    {row.deviation === "expired" && <input type="date" aria-label={`Срок годности: ${itemTitle(row.line)}`} value={row.expiry} disabled={locked} onChange={(event) => edit(row.line.id, { expiry: event.target.value })} />}
                  </td>
                  {done && <td className="num">{row.ticked ? (result?.grn ? <strong>{result.grn}</strong> : <StatusChip status={result?.status} />) : ""}{result && Number(result.backorder_quantity) > 0 && <span className="delivery-short">недовоз {result.backorder_quantity} {result.unit} → дозавоз</span>}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {upd && updRows.length > 0 && <UpdRowsTable rows={updRows} lines={rows} disabled={busy || done}
        onAssign={(row, target) => setRows((current) => reassignRow(current, row, target))} />}
      {done && <ReceiptLabels lots={receiptLots(chosen.map((row) => results.get(row.line.id)))} />}
      <div className="header-actions">
        {!done && <button type="submit" className="primary small" disabled={busy || !chosen.length}>{busy ? "Проводим…" : `Провести поставку (${chosen.length} поз.)`}</button>}
        <button type="button" className="secondary small" disabled={busy} onClick={onClose}>{done ? "Закрыть" : "Отмена"}</button>
      </div>
    </form>
  );
}

/** «Строки УПД»: где каждая строка документа — без пары первыми; «Принять как…» переносит строку на позицию. */
function UpdRowsTable({ rows, lines, disabled, onAssign }: {
  rows: UpdProposal["rows"];
  lines: DeliveryRow[];
  disabled: boolean;
  onAssign: (row: UpdProposal["rows"][number], target: string) => void;
}) {
  const ordered = [...rows].sort((a, b) => Number(rowTargets(lines, a.index).length > 0) - Number(rowTargets(lines, b.index).length > 0) || a.index - b.index);
  return (
    <section className="upd-rows" aria-label="Строки УПД">
      <h3>Строки УПД</h3>
      <div className="table-wrap">
        <table className="delivery-table">
          <thead><tr><th>№</th><th>Наименование в УПД</th><th>Кол-во</th><th>Позиция приёмки</th></tr></thead>
          <tbody>
            {ordered.map((row) => {
              const targets = rowTargets(lines, row.index);
              const value = targets.length === 1 ? targets[0] : targets.length > 1 ? "shared" : "";
              return (
                <tr key={row.index} className={targets.length ? "" : "upd-row-free"}>
                  <td className="num">{row.index + 1}</td>
                  <td className="name">{rowTitle(row) || "—"}</td>
                  <td className="num">{row.quantity ? `${row.quantity} ${row.unit}` : "не прочитано"}</td>
                  <td>
                    <select aria-label={`Позиция приёмки для строки УПД ${row.index + 1}`} value={value} disabled={disabled}
                      onChange={(event) => onAssign(row, event.target.value)}>
                      <option value="">{targets.length ? "— не принимать —" : "нет в ожидании — «Склад → Поставка»"}</option>
                      {targets.length > 1 && <option value="shared" disabled>делится на {targets.length} поз.</option>}
                      {lines.map((item) => <option key={item.line.id} value={item.line.id}>{[item.line.order_number, itemTitle(item.line)].filter(Boolean).join(" · ")}</option>)}
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
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

/** «Принять по УПД»: скан (сканер ПК склада, файл, «+ Страница») → распознанный УПД поверх листа ожидания → поставка
 *  этого поставщика открывается сама, с отмеченными строками. `start` — уже распознанный УПД («Склад → Поставка»),
 *  `group` — группа «Ожидаются», с которой начали (её поставщик, если в УПД он не распознан). */
export function UpdDeliveryWindow({ group, start, onClose, onDone }: {
  group?: ExpectedGroup;
  start?: UpdDelivery;
  onClose: () => void;
  onDone: (lines: ReceivingLine[]) => void;
}) {
  const [delivery, setDelivery] = useState<UpdDelivery | null>(start ?? null);
  const [matching, setMatching] = useState(false);
  const [error, setError] = useState("");
  const [empty, setEmpty] = useState("");
  const accepted = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function propose(scan: WarehouseReceiptScan, pages: number, edo: boolean, warehouse?: string) {
    setMatching(true);
    setError("");
    setEmpty("");
    try {
      const proposal = await receivingApi.updProposal(scan, { warehouse: warehouse ?? group?.warehouse, supplier: group?.supplier });
      if (!proposal.lines.length) {
        setDelivery(null);
        setEmpty(`В листе ожидания нет позиций ${scan.recognised.supplier ? `поставщика «${scan.recognised.supplier}»` : "этого УПД"}${scan.recognised.invoice_number ? ` и счёта ${scan.recognised.invoice_number}` : ""} — оформите поставку в «Склад → Поставка».`);
        return;
      }
      setDelivery({ scan, proposal, pages, edo });
    } catch (reason) {
      setError(failureText(reason, "УПД не сопоставлен с листом ожидания"));
    } finally {
      setMatching(false);
    }
  }

  const scan = useUpdScan(receivingApi.updScan, (answer, pages) => void propose(answer, pages, false));
  const phone = typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
  const busy = scan.scanning || matching;

  return (
    <PageWindow label="Принять по УПД" width="min(1680px, calc(100vw - 32px))" close={() => { if (!busy || accepted.current) onClose(); }}>
      {delivery ? (
        <DeliveryPanel key={`${delivery.scan.scan_file}:${delivery.proposal.warehouse}`} group={proposalGroup(delivery.proposal)} upd={delivery}
          onClose={onClose} onDone={(lines) => { accepted.current = true; onDone(lines); }}
          onWarehouse={(warehouse) => void propose(delivery.scan, delivery.pages, delivery.edo, warehouse)} />
      ) : (
        <section className="panel delivery-panel upd-scan-step">
          <div className="upd-head"><h2>Принять по УПД</h2>{group && <span className="upd-ids">{group.supplier} · {group.warehouse}</span>}</div>
          <p className="page-sub">Отсканируйте УПД или выберите файл (PDF, фото, ЭДО XML/ZIP) — поставка из листа ожидания откроется сама.</p>
          <div className="header-actions">
            {scan.pages.length > 0
              ? <button type="button" className="secondary" disabled={busy} onClick={() => void scan.fromScanner()}>+ Страница</button>
              : <button type="button" className="primary" disabled={busy} onClick={() => void scan.fromScanner(true)}>Сканировать</button>}
            <button type="button" className="secondary" disabled={busy} onClick={() => fileInput.current?.click()}>{scan.scanning && !scan.status ? "Разбираем УПД…" : "Файл…"}</button>
            <input ref={fileInput} type="file" hidden multiple aria-label="Файл УПД" accept=".pdf,.xml,.zip,image/*,application/xml,text/xml,application/zip" {...(phone ? { capture: "environment" as const } : {})}
              onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ""; if (files.length) void scan.fromFiles(files); }} />
            <button type="button" className="secondary" disabled={scan.scanning} onClick={onClose}>Отмена</button>
          </div>
          {(scan.statusText || matching) && <p className={`wr-scan-status${scan.error ? " error" : ""}`} role="status">
            {matching ? "Ищу позиции в листе ожидания…" : scan.statusText}
            {scan.status ? <> <a href="#" onClick={(event) => { event.preventDefault(); scan.cancel(); }}>Отменить</a></> : null}
          </p>}
          {error && <div className="error-banner" role="alert">{error}</div>}
          {empty && <div className="warning-inline" role="status">{empty}</div>}
        </section>
      )}
    </PageWindow>
  );
}
