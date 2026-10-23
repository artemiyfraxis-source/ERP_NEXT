import { Fragment, useEffect, useMemo, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { ApiError, warehouseApi, type WarehouseLot, type WarehouseReceipt as ReceiptDoc, type WarehouseReceiptScan } from "./api";
import { labelsApi } from "./LabelPrint";
import { PageWindow } from "./PageWindow";
import { SuggestInput } from "./SuggestInput";
import { BUSY_TITLE, PlaceHint, usePlaceHint } from "./WarehousePlaceHint";
import {
  GRID_COLUMNS, LABELS_MAX, RECEIPT_LIMIT, applyHints, applyReceiptPaste, applyScan, buildAccept, canAccept, editCell,
  DEFAULT_PURPOSE, emptyHeader, emptyLine, fillEmpty, headerChanged, headerFields, headerFromReceipt, isBlankLine, lineFromLot, lineSummary,
  mergeHeader, shortDate, withTrailingBlank,
  type HeaderKey, type LineKey, type ReceiptHeader, type ReceiptLine,
} from "./warehouseReceiptLogic";

const norm = (value: string) => value.trim().replace(/\s+/g, " ").toLocaleLowerCase("ru");

type Props = {
  knownLots: WarehouseLot[];
  typeChoices: string[];
  onClose: () => void;
  /** a lot was accepted: the warehouse list reloads */
  onAccepted: (lot: WarehouseLot) => void;
};

/** «Принять позицию»: the place and the number of labels; a 409 (the article is awaited in «Приёмка») asks to confirm. */
function AcceptDialog({ line, warehouse, onClose, onAccept }: {
  line: ReceiptLine;
  warehouse: string;
  onClose: () => void;
  onAccept: (place: string, labels: number, confirmExpected: boolean) => Promise<void>;
}) {
  const [place, setPlace] = useState("");
  const [labels, setLabels] = useState("1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState("");
  const placeInput = useRef<HTMLInputElement>(null);
  const hint = usePlaceHint(warehouse, place);
  // PageWindow focuses its own box after the children mount: «Место» takes the focus back
  useEffect(() => { const timer = window.setTimeout(() => placeInput.current?.focus(), 0); return () => window.clearTimeout(timer); }, []);
  const count = Math.floor(Number(labels));
  const labelsOk = Number.isFinite(count) && count >= 1 && count <= LABELS_MAX;

  async function send(confirm: boolean) {
    if (!place.trim()) { setError("Укажите место"); return; }
    if (!labelsOk) { setError(`Этикеток от 1 до ${LABELS_MAX}`); return; }
    setBusy(true);
    setError("");
    try {
      await onAccept(place.trim(), count, confirm);
      onClose();
    } catch (reason) {
      const text = reason instanceof Error ? reason.message : "Не удалось принять позицию";
      if (reason instanceof ApiError && reason.status === 409) { setConflict(text); setError(""); } else { setConflict(""); setError(text); }
    } finally {
      setBusy(false);
    }
  }

  return <PageWindow label="Принять позицию" width={520} close={() => { if (!busy) onClose(); }}>
    <form className="panel wr-accept" noValidate onSubmit={(event) => { event.preventDefault(); void send(false); }}>
      <div className="manual-stock-head"><h2>Принять позицию</h2></div>
      <p className="wr-summary">{lineSummary(line)}</p>
      <label>Место *
        <input ref={placeInput} autoFocus aria-label="Место хранения" placeholder="1F4" className={hint.busy ? "place-busy" : undefined}
          title={hint.busy ? BUSY_TITLE : undefined} value={place} onChange={(event) => { setPlace(event.target.value); setConflict(""); }}
          onKeyDown={(event) => { if (event.key === "Enter" && !conflict && !busy) { event.preventDefault(); void send(false); } }} />
      </label>
      {line.places.length > 0 && <p className="wr-places">уже лежит:{" "}
        {line.places.map((code) => <button key={code} type="button" className="link-button wr-chip" onClick={() => setPlace(code)}>{code}</button>)}
      </p>}
      <PlaceHint {...hint} place={place} onPick={setPlace} />
      <label>Этикеток
        <input aria-label="Этикеток" type="number" min={1} max={LABELS_MAX} step={1} value={labels} onChange={(event) => setLabels(event.target.value)} />
      </label>
      <p className="page-sub">сколько пакетиков — столько этикеток</p>
      {error && <p className="error-banner" role="alert">{error}</p>}
      {conflict && <p className="warning-inline" role="alert">{conflict}</p>}
      <div className="manual-stock-actions">
        {conflict
          ? <button type="button" className="primary" disabled={busy} onClick={() => void send(true)}>Всё равно принять</button>
          : <button type="submit" className="primary" disabled={busy}>{busy ? "Принимаем…" : "Принять"}</button>}
        <button type="button" className="secondary" disabled={busy} onClick={onClose}>Отмена</button>
      </div>
    </form>
  </PageWindow>;
}

const SCAN_POLL_MS = 1500;
const SCAN_WAIT_MS = 130_000;

/** «Внести позиции»: the supply header and a paste grid; every row is accepted on its own with «Принять». */
export function WarehouseReceipt({ knownLots, typeChoices, onClose, onAccepted }: Props) {
  const [header, setHeader] = useState<ReceiptHeader>(emptyHeader);
  const [lines, setLines] = useState<ReceiptLine[]>(() => [emptyLine()]);
  const [receipt, setReceipt] = useState<ReceiptDoc | null>(null);
  const [scanFile, setScanFile] = useState("");
  const [recent, setRecent] = useState<ReceiptDoc[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scanStatus, setScanStatus] = useState<"" | "pending" | "claimed">("");
  const [scanError, setScanError] = useState("");
  const scanJobId = useRef<string | null>(null);
  const scanAborted = useRef(false);
  const [accepting, setAccepting] = useState<number | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const pendingFocus = useRef<{ row: number; key: LineKey } | null>(null);
  const timers = useRef(new Map<number, number>());
  const headerRef = useRef(header);
  headerRef.current = header;
  const acceptedCount = lines.filter((line) => line.accepted).length;
  const pendingCount = lines.filter((line) => !line.accepted && !isBlankLine(line)).length;
  const phone = typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;

  useEffect(() => {
    let cancelled = false;
    void warehouseApi.receipts(20).then(({ receipts }) => { if (!cancelled) setRecent(receipts); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, []);
  useEffect(() => () => timers.current.forEach((timer) => window.clearTimeout(timer)), []);

  const knownByArticle = useMemo(() => {
    const byKey = new Map<string, WarehouseLot>();
    for (const lot of knownLots) {
      const key = norm(lot.article);
      if (key && !byKey.has(key)) byKey.set(key, lot);
    }
    return byKey;
  }, [knownLots]);
  const knownArticles = useMemo(() => new Map([...knownByArticle.values()].map((lot) => [lot.article.trim(), lot.name])), [knownByArticle]);
  const suppliers = useMemo(() => {
    const names = [...recent.map((item) => item.supplier), ...knownLots.map((lot) => lot.receipt?.supplier ?? "")];
    return [...new Set(names.map((name) => name.trim()).filter(Boolean))];
  }, [recent, knownLots]);

  useEffect(() => {
    const target = pendingFocus.current;
    if (!target) return;
    const element = cell(target.row, target.key);
    if (element) {
      pendingFocus.current = null;
      element.focus();
      if (element instanceof HTMLInputElement) element.select();
    }
  });

  function cell(row: number, key: LineKey) {
    return root.current?.querySelector<HTMLElement>(`[data-cell="${row}:${key}"]`) ?? null;
  }

  const setHead = (key: HeaderKey, value: string) => setHeader((current) => ({ ...current, [key]: value }));
  const patchLine = (row: number, change: (line: ReceiptLine) => ReceiptLine) =>
    setLines((current) => withTrailingBlank(current.map((line, index) => (index === row ? change(line) : line))));

  /** Article lookup: a known lot at once, the catalog after 250 ms. Fills EMPTY cells only. */
  function lookup(row: number, article: string) {
    const text = article.trim();
    window.clearTimeout(timers.current.get(row));
    timers.current.delete(row);
    if (!text) return;
    const same = (line: ReceiptLine) => !line.accepted && norm(line.article) === norm(text);
    const known = knownByArticle.get(norm(text));
    if (known) {
      patchLine(row, (line) => same(line)
        ? fillEmpty(line, { name: known.name, series: known.series, item_type: known.item_type, unit: known.unit, manufacturer: known.manufacturer }, "по уже внесённой партии")
        : line);
    }
    timers.current.set(row, window.setTimeout(() => {
      const { supplier, invoice_number, warehouse } = headerRef.current;
      void warehouseApi.articleInfo(text, { supplier, invoice_number, warehouse }).then((hints) => {
        patchLine(row, (line) => same(line) ? applyHints(line, hints) : line);
      }).catch(() => undefined);
    }, 250));
  }

  function setCell(row: number, key: LineKey, value: string) {
    patchLine(row, (line) => editCell(line, key, value));
    if (key === "article") {
      patchLine(row, (line) => ({ ...line, places: [], expected: "" }));
      lookup(row, value);
    }
  }

  function move(row: number, key: LineKey, delta: number) {
    const next = row + delta;
    if (next < 0) return;
    if (next >= lines.length) return;
    const element = cell(next, key);
    element?.focus();
    if (element instanceof HTMLInputElement) element.select();
  }

  function onKeyDown(event: KeyboardEvent, row: number, key: LineKey) {
    const target = event.target as HTMLElement;
    const open = target.getAttribute("aria-expanded") === "true";
    if (open && (event.key === "Enter" || event.key === "Escape")) return; // the open suggestion list takes it
    const isDate = target instanceof HTMLInputElement && target.type === "date";
    if (event.key === "Enter") {
      event.preventDefault();
      move(row, key, event.shiftKey ? -1 : 1);
    } else if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !isDate && !event.altKey && !open) {
      event.preventDefault();
      move(row, key, event.key === "ArrowDown" ? 1 : -1);
    }
  }

  function onPaste(event: ClipboardEvent, row: number, key: LineKey) {
    const text = event.clipboardData.getData("text/plain");
    if (!/[\t\n]/.test(text.replace(/\r?\n$/, ""))) return;
    event.preventDefault();
    const result = applyReceiptPaste(lines, row, key, text);
    if ("error" in result) { setNote(result.error); return; }
    setLines(withTrailingBlank(result.lines));
    const merged = mergeHeader(header, result.header);
    const headerFilled = (Object.keys(result.header) as HeaderKey[]).filter((name) => merged[name] !== header[name]).length;
    setHeader(merged);
    setError("");
    setNote(
      `Вставлено строк: ${result.filled}`
      + (headerFilled ? `, из таблицы заполнена шапка (${headerFilled})` : "")
      + (result.dropped ? `. Не поместилось: ${result.dropped} (в поставке до ${RECEIPT_LIMIT} строк)` : "")
      + (result.unknown.length ? `. Не распознаны колонки: ${result.unknown.join(", ")}` : ""),
    );
    result.lines.forEach((line, index) => {
      if (line !== lines[index] && !line.accepted && line.article.trim()) lookup(index, line.article);
    });
  }

  function applyScanResult(result: WarehouseReceiptScan) {
    const next = applyScan(header, lines, result);
    setHeader(next.header);
    setLines(withTrailingBlank(next.lines));
    setScanFile(result.scan_file);
    setNote(result.note || `Распознано строк: ${next.added}`);
    next.lines.forEach((line, index) => { if (line !== lines[index] && line.article.trim()) lookup(index, line.article); });
  }

  async function scan(file: File) {
    setScanning(true);
    setError("");
    try {
      applyScanResult(await warehouseApi.scanReceipt(file));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось разобрать УПД");
    } finally {
      setScanning(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  /** «Сканировать»: задание уходит на ПК склада, ответ опрашиваем каждые 1,5 с, не дольше 130 с. */
  async function scanFromScanner() {
    setScanning(true);
    setError("");
    setScanError("");
    setScanStatus("pending");
    scanAborted.current = false;
    scanJobId.current = null;
    try {
      const job = await warehouseApi.startScanJob();
      scanJobId.current = job.id;
      const deadline = Date.now() + SCAN_WAIT_MS;
      let current = job;
      for (;;) {
        if (scanAborted.current) return;
        if (current.status === "done" && current.result) { applyScanResult(current.result); return; }
        if (current.status === "failed") { setScanError(current.error || "Сканер не ответил"); return; }
        setScanStatus(current.status === "claimed" ? "claimed" : "pending");
        if (Date.now() >= deadline) {
          setScanError("ПК склада не ответил за 130 с. Проверьте, что он включён и агент запущен");
          void warehouseApi.cancelScanJob(job.id).catch(() => undefined);
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, SCAN_POLL_MS));
        if (scanAborted.current) return;
        current = await warehouseApi.scanJob(job.id);
      }
    } catch (reason) {
      if (!scanAborted.current) setScanError(reason instanceof Error ? reason.message : "Не удалось отсканировать УПД");
    } finally {
      scanJobId.current = null;
      setScanStatus("");
      setScanning(false);
    }
  }

  function cancelScan() {
    scanAborted.current = true;
    const id = scanJobId.current;
    if (id) void warehouseApi.cancelScanJob(id).catch(() => undefined);
    setScanStatus("");
    setScanning(false);
  }

  async function resume(id: string) {
    if (!id) return;
    if (pendingCount > 0 && !window.confirm("Несохранённые строки таблицы будут заменены. Продолжить?")) return;
    setError("");
    try {
      const { receipt: saved, lots } = await warehouseApi.receipt(id);
      setReceipt(saved);
      setHeader(headerFromReceipt(saved));
      setScanFile(saved.scan_file ?? "");
      setLines(withTrailingBlank(lots.map(lineFromLot).slice(0, RECEIPT_LIMIT)));
      setNote(`Поставка продолжена: принято позиций — ${lots.length}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось открыть поставку");
    }
  }

  /** The supply is created on the first acceptance; later header edits go up with the version. */
  async function ensureReceipt(): Promise<ReceiptDoc> {
    const fields = headerFields(headerRef.current, scanFile);
    try {
      if (!receipt) {
        const created = await warehouseApi.createReceipt(fields);
        setReceipt(created);
        return created;
      }
      if (!headerChanged(receipt, headerRef.current, scanFile)) return receipt;
      const updated = await warehouseApi.updateReceipt(receipt.id, { ...fields, version: receipt.version });
      setReceipt(updated);
      return updated;
    } catch (reason) {
      // not the dialog's «ожидается в Приёмке» conflict: a plain failure of the header
      throw reason instanceof ApiError ? new Error(reason.message) : reason;
    }
  }

  async function accept(row: number, place: string, labels: number, confirmExpected: boolean) {
    const line = lines[row];
    const saved = await ensureReceipt();
    const result = await warehouseApi.acceptReceiptLot(saved.id, buildAccept(line, headerRef.current, place, labels, confirmExpected));
    patchLine(row, (current) => ({
      ...current,
      error: "",
      // what went to the server stays on the row
      unit: current.unit.trim() || "шт",
      purpose: current.purpose.trim() || headerRef.current.purpose.trim() || DEFAULT_PURPOSE,
      accepted: {
        lotId: result.lot.id,
        grn: result.lot.grn,
        place: result.lot.storage_location || place,
        labels: !result.label_error ? labels : 0,
        labelError: result.label_error,
      },
    }));
    if (scroller.current) scroller.current.scrollLeft = 0;
    onAccepted(result.lot);
  }

  async function reprint(row: number) {
    const accepted = lines[row].accepted;
    if (!accepted) return;
    try {
      await labelsApi.printLot(accepted.lotId, 1);
      patchLine(row, (line) => (line.accepted ? { ...line, accepted: { ...line.accepted, labels: 1, labelError: "" } } : line));
    } catch (reason) {
      const text = reason instanceof Error ? reason.message : "Не удалось отправить этикетку";
      patchLine(row, (line) => (line.accepted ? { ...line, accepted: { ...line.accepted, labels: 0, labelError: text } } : line));
    }
  }

  function control(row: number, key: LineKey, line: ReceiptLine) {
    const label = GRID_COLUMNS.find((column) => column.key === key)!.label;
    const muted = line.auto.includes(key);
    const common = {
      "data-cell": `${row}:${key}`,
      "aria-label": `${label}, строка ${row + 1}`,
      "aria-invalid": line.error ? true : undefined,
      className: [line.error ? "rg-bad" : "", muted ? "wr-auto" : ""].filter(Boolean).join(" ") || undefined,
      onKeyDown: (event: KeyboardEvent) => onKeyDown(event, row, key),
      onPaste: (event: ClipboardEvent) => onPaste(event, row, key),
    };
    const value = line[key];
    const set = (next: string) => setCell(row, key, next);
    if (key === "article") {
      return <SuggestInput {...common} options={[...knownArticles.keys()]} describe={(option) => knownArticles.get(option) ?? ""} minLength={2} value={value} onChange={set} />;
    }
    if (key === "item_type") return <SuggestInput {...common} options={typeChoices} value={value} onChange={set} />;
    if (key === "expiry_date") return !line.article.trim() ? null : <input {...common} type="date" value={value} onChange={(event) => set(event.target.value)} />;
    return <input
      {...common}
      inputMode={key === "quantity" ? "decimal" : undefined}
      placeholder={isBlankLine(line) ? undefined : key === "purpose" ? header.purpose.trim() || DEFAULT_PURPOSE : key === "unit" ? "шт" : undefined}
      value={value}
      onChange={(event) => set(event.target.value)}
    />;
  }

  const headerField = (key: HeaderKey, label: string, type = "text") => (
    <label>{label}
      <input type={type} value={header[key]} placeholder={key === "purpose" ? DEFAULT_PURPOSE : undefined} onChange={(event) => setHead(key, event.target.value)} />
    </label>
  );
  const colSpan = GRID_COLUMNS.length + 2;

  return (
    <PageWindow label="Внести позиции" width="min(2200px, calc(100vw - 32px))" close={onClose}>
      <div className="panel wr-form" ref={root}>
        <div className="manual-stock-head">
          <h2>Поставка</h2>
          {recent.length > 0 && <label className="inline-select wr-resume">Продолжить поставку
            <select aria-label="Продолжить поставку" value="" onChange={(event) => void resume(event.target.value)}>
              <option value="">— выбрать —</option>
              {recent.map((item) => <option key={item.id} value={item.id}>
                {[item.supplier || "без поставщика", item.upd_number ? `УПД ${item.upd_number}` : "", item.invoice_number ? `счёт ${item.invoice_number}` : "", shortDate(item.created_at)].filter(Boolean).join(" · ")}
              </option>)}
            </select>
          </label>}
        </div>
        <div className="wr-head">
          <label>Склад
            <select aria-label="Склад поставки" value={header.warehouse} onChange={(event) => setHead("warehouse", event.target.value)}>
              <option value="ЦАР">ЦАР</option><option value="ЖУК">ЖУК</option>
            </select>
          </label>
          <label className="wr-supplier">Поставщик
            <SuggestInput aria-label="Поставщик" options={suppliers} value={header.supplier} onChange={(value) => setHead("supplier", value)} />
          </label>
          {headerField("invoice_number", "№ счёта")}
          {headerField("invoice_date", "Дата счёта", "date")}
          {headerField("upd_number", "№ УПД")}
          {headerField("upd_date", "Дата УПД", "date")}
          {headerField("purpose", "Назначение")}
          <div className="wr-scan">
            <button type="button" className="secondary" disabled={scanning} onClick={() => void scanFromScanner()}>Сканировать</button>
            <button type="button" className="secondary" disabled={scanning} onClick={() => fileInput.current?.click()}>{scanning && !scanStatus ? "Разбираем УПД…" : "Файл…"}</button>
            <input ref={fileInput} type="file" hidden aria-label="Файл УПД" accept=".pdf,image/*" {...(phone ? { capture: "environment" as const } : {})}
              onChange={(event) => { const file = event.target.files?.[0]; if (file) void scan(file); }} />
          </div>
        </div>
        {scanStatus || scanError ? (
          <p className={`wr-scan-status${scanError ? " error" : ""}`} role="status">
            {scanError || (scanStatus === "claimed" ? "Сканирует…" : "Положите УПД на стекло сканера — ждём ПК склада…")}
            {scanStatus ? <> <a href="#" onClick={(event) => { event.preventDefault(); cancelScan(); }}>Отменить</a></> : null}
          </p>
        ) : null}
        <p className="rg-hint">
          Ctrl+V — вставка блока из Excel · Enter — вниз, Tab — вправо · пустое назначение — из шапки · до {RECEIPT_LIMIT} строк
        </p>
        <div className="rg">
          <div className="rg-scroll wr-scroll" ref={scroller}>
            <table className="rg-table wr-table" aria-label="Позиции поставки">
              <thead>
                <tr>
                  <th className="rg-no wr-stick wr-stick-no">№</th>
                  {GRID_COLUMNS.map((column) => <th key={column.key} className={`wr-col-${column.key}${column.key === "article" ? " wr-stick wr-stick-article" : ""}`}>{column.label}{column.required ? " *" : ""}</th>)}
                  <th className="wr-col-act wr-stick wr-stick-act" />
                </tr>
              </thead>
              <tbody>
                {lines.map((line, row) => {
                  const done = line.accepted;
                  return <Fragment key={row}>
                    <tr className={[line.error ? "rg-row-bad" : "", done ? "wr-done" : ""].filter(Boolean).join(" ") || undefined}
                      title={line.auto.length && line.source ? `Подставлено: ${line.source}` : undefined}>
                      <td className="rg-no wr-stick wr-stick-no">{row + 1}</td>
                      {GRID_COLUMNS.map((column) => <td key={column.key} className={`wr-col-${column.key}${column.key === "article" ? " wr-stick wr-stick-article" : ""}`}>
                        {done ? <span className="wr-ro" title={line[column.key]}>{column.key === "expiry_date" ? shortDate(line[column.key]) : line[column.key]}</span> : control(row, column.key, line)}
                      </td>)}
                      <td className="wr-col-act wr-stick wr-stick-act">
                        {done
                          ? <span className="wr-accepted">✓ Принято</span>
                          : !isBlankLine(line) && <>
                            <button type="button" className="rg-link wr-accept-btn" disabled={!canAccept(line) || accepting !== null} aria-label={`Принять строку ${row + 1}`}
                              onClick={() => setAccepting(row)}>Принять</button>
                            <button type="button" className="rg-link" aria-label={`Убрать строку ${row + 1}`}
                              onClick={() => setLines((current) => withTrailingBlank(current.filter((_, index) => index !== row)))}>Убрать</button>
                          </>}
                      </td>
                    </tr>
                    {done && <tr className="wr-done-row"><td colSpan={colSpan}>
                      <span className="wr-sub">
                        <span className="wr-accepted">Принято · ГРН {done.grn} · место {done.place || "не указано"}{done.labels ? ` · этикеток ${done.labels}` : ""}</span>
                        <button type="button" className="rg-link" onClick={() => void reprint(row)}>Этикетка ещё раз</button>
                        {done.labelError && <small className="danger-text" role="alert">{done.labelError}</small>}
                      </span>
                    </td></tr>}
                    {!done && line.expected && <tr className="wr-note-row"><td colSpan={colSpan}>
                      <span className="warning-inline">ожидается в Приёмке: {line.expected}</span>
                    </td></tr>}
                  </Fragment>;
                })}
              </tbody>
            </table>
          </div>
          {error && <p className="error-banner" role="alert">{error}</p>}
          {note && <p className="page-sub" role="status">{note}</p>}
          <div className="rg-bar">
            <span className="rg-count">Строк: <b>{lines.length}</b> из {RECEIPT_LIMIT} · принято <b>{acceptedCount}</b> · к приёмке <b>{pendingCount}</b></span>
            <span className="rg-spacer" />
            <button type="button" className="secondary" onClick={onClose}>Закрыть</button>
          </div>
        </div>
      </div>
      {accepting !== null && lines[accepting] && <AcceptDialog
        line={lines[accepting]}
        warehouse={header.warehouse}
        onClose={() => setAccepting(null)}
        onAccept={(place, labels, confirm) => accept(accepting, place, labels, confirm)}
      />}
    </PageWindow>
  );
}
