import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type RefObject } from "react";
import { ApiError, warehouseApi, type WarehouseLot, type WarehouseScan as ScanAnswer, type WarehouseUndoPreview } from "./api";
import { LotLabel } from "./LabelPrint";

type Mutate = (key: string, operation: () => Promise<unknown>) => Promise<void>;

function failure(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

function lotLabel(lot: WarehouseLot) {
  return `${lot.grn}${lot.package ? ` / ${lot.package}` : ""}`;
}

/** A scan request from the stock search field: the code and when Enter was pressed (the same code twice opens twice). */
export type ScanRequest = { code: string; at: number };

/** Scan field (Roller_Buyer /stock and /tsd), owner 22.10: one field for everything — the stock search.
 *  Typing filters the table; Enter (a hardware scanner sends it) asks the scan route: a ГРН opens the lot card, a place
 *  code lists what lies there, anything else («резистор») is no code and just stays a filter. After every action the
 *  field takes the focus back, so the next scan goes straight in. */
export function WarehouseScanResults({ request, inputRef, readOnly, disabled, mutate, onClose }: {
  request: ScanRequest | null;
  inputRef: RefObject<HTMLInputElement | null>;
  readOnly: boolean;
  disabled: boolean;
  mutate: Mutate;
  onClose: () => void;
}) {
  const [answer, setAnswer] = useState<ScanAnswer | null>(null);
  const [lot, setLot] = useState<WarehouseLot | null>(null);
  const [error, setError] = useState("");
  // «Назначение / откуда» is the same for a run of scans (one project, one person): kept between cards.
  const [who, setWho] = useState("");

  function refocus() {
    window.setTimeout(() => { inputRef.current?.focus(); inputRef.current?.select(); }, 0);
  }

  useEffect(() => {
    setAnswer(null);
    setLot(null);
    setError("");
    const text = request?.code.trim();
    if (!text) return;
    let live = true;
    warehouseApi.scan(text).then((found) => {
      if (!live) return;
      if (found.kind === "lot" && found.lots.length === 1) setLot(found.lots[0]);
      else setAnswer(found);
    }, (reason) => {
      if (!live) return;
      // 404 — not a ГРН or a place: the words stay a filter of the table, nothing to say.
      if (!(reason instanceof ApiError && reason.status === 404)) setError(failure(reason, "Скан не выполнен"));
      refocus();
    });
    return () => { live = false; };
  }, [request]);

  function closeAll() {
    setAnswer(null);
    setLot(null);
    setError("");
    onClose();
    refocus();
  }

  function changed(updated: WarehouseLot) {
    setLot(updated);
    setAnswer((current) => current && { ...current, lots: current.lots.map((item) => item.id === updated.id ? updated : item) });
    refocus();
  }

  if (!error && !answer && !lot) return null;
  return <section className="scan-zone" aria-label="Скан">
    {error && <div className="error-banner" role="alert">{error}</div>}
    {answer && !lot && <ScanList answer={answer} onOpen={setLot} onClose={closeAll} />}
    {lot && <ScanLotCard
      lot={lot}
      readOnly={readOnly}
      disabled={disabled}
      mutate={mutate}
      who={who}
      setWho={setWho}
      onChanged={changed}
      onBack={answer ? () => setLot(null) : undefined}
      onClose={closeAll}
    />}
  </section>;
}

function ScanList({ answer, onOpen, onClose }: { answer: ScanAnswer; onOpen: (lot: WarehouseLot) => void; onClose: () => void }) {
  const title = answer.kind === "place" ? `Место ${answer.place}` : `ГРН ${answer.code}`;
  return <div className="panel scan-panel" role="region" aria-label={title} onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
    <div className="scan-panel-head">
      <h2>{title}{answer.lots.length ? ` — партий: ${answer.lots.length}` : ""}</h2>
      <button type="button" className="link-button" onClick={onClose}>Закрыть</button>
    </div>
    {answer.lots.length ? <div className="table-wrap"><table className="grid compact" aria-label="Партии">
      <thead><tr><th>ГРН</th><th>Наименование</th><th className="num">Остаток</th><th>Место</th><th>Склад</th></tr></thead>
      <tbody>{answer.lots.map((item) => <tr key={item.id}>
        <td><button type="button" className="link-button scan-open" onClick={() => onOpen(item)}>{lotLabel(item)}</button></td>
        <td className="text">{item.name || item.article}</td>
        <td className="num">{item.balance} {item.unit}</td>
        <td>{item.storage_location || "—"}</td>
        <td>{item.warehouse}</td>
      </tr>)}</tbody>
    </table></div> : <div className="empty">{answer.kind === "place" ? `На месте ${answer.place} ничего не лежит.` : "Партий нет."}</div>}
  </div>;
}

function ScanLotCard({ lot, readOnly, disabled, mutate, who, setWho, onChanged, onBack, onClose }: {
  lot: WarehouseLot;
  readOnly: boolean;
  disabled: boolean;
  mutate: Mutate;
  who: string;
  setWho: (value: string) => void;
  onChanged: (lot: WarehouseLot) => void;
  onBack?: () => void;
  onClose: () => void;
}) {
  const [quantity, setQuantity] = useState("");
  const [place, setPlace] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const quantityRef = useRef<HTMLInputElement>(null);
  const [label, setLabel] = useState(false);
  useEffect(() => { setError(""); setDone(""); setLabel(false); }, [lot.id]);

  async function run(action: string, operation: () => Promise<{ lot: WarehouseLot }>, message: (updated: WarehouseLot) => string) {
    setError("");
    setDone("");
    const box: { lot?: WarehouseLot } = {};
    try {
      await mutate(lot.id, async () => { box.lot = (await operation()).lot; });
    } catch (reason) {
      setError(failure(reason, `${action}: не выполнено`));
      return;
    }
    const updated = box.lot;
    if (!updated) { setError("Склад обновляется — повтори через секунду."); return; }  // the page was busy: nothing sent
    setQuantity("");
    setPlace("");
    setDone(message(updated));
    onChanged(updated);
  }

  function needs(field: "quantity" | "who") {
    if (field === "quantity" && !(Number(quantity.replace(",", ".")) > 0)) { setError("Укажи количество."); quantityRef.current?.focus(); return false; }
    if (field === "who" && !who.trim()) { setError("Укажи назначение: номер проекта или фамилию."); return false; }
    return true;
  }

  const amount = () => quantity.replace(",", ".").trim();
  const unit = lot.unit || "шт";
  const writeOff = () => {
    if (!needs("quantity") || !needs("who")) return;
    void run("Списание", () => warehouseApi.writeOff(lot.id, lot.version, amount(), who.trim(), ""),
      (updated) => `Списано ${amount()} ${unit} → остаток ${updated.balance} ${unit}`);
  };
  const giveBack = () => {
    if (!needs("quantity") || !needs("who")) return;
    void run("Возврат", () => warehouseApi.returnWriteOff(lot.id, lot.version, amount(), who.trim(), ""),
      (updated) => `Возвращено ${amount()} ${unit} → остаток ${updated.balance} ${unit}`);
  };
  const move = () => {
    if (!place.trim()) { setError("Укажи новое место."); return; }
    void run("Перемещение", () => warehouseApi.move(lot.id, lot.version, place.trim()),
      (updated) => `Перемещено → ${updated.storage_location || "без места"}`);
  };
  const keys = (event: ReactKeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } };

  return <div className="panel scan-card" role="region" aria-label={`Карточка ${lotLabel(lot)}`} onKeyDown={keys}>
    <div className="scan-panel-head">
      <h2>ГРН {lotLabel(lot)}</h2>
      <div className="header-actions">
        {onBack && <button type="button" className="link-button" onClick={onBack}>← К списку</button>}
        <button type="button" className="link-button" onClick={onClose}>Закрыть (Esc)</button>
      </div>
    </div>
    <p className="lot-card-meta"><span>{lot.name || lot.article}</span>{lot.name && lot.article !== lot.name && <span>{lot.article}</span>}{lot.purpose && <span>{lot.purpose}</span>}</p>
    <div className="lot-card-main">
      <div><span className="lot-card-big">{lot.balance} {unit}</span><small>остаток{lot.available !== lot.balance ? ` · доступно ${lot.available}` : ""}{Number(lot.written_off) > 0 ? ` · списано ${lot.written_off}` : ""}</small></div>
      <div><span className="lot-card-big">{lot.storage_location || "без места"}</span><small>{lot.warehouse}</small></div>
    </div>
    {!readOnly && <>
      <form className="scan-actions" aria-label="Быстрые действия" onSubmit={(event) => { event.preventDefault(); writeOff(); }}>
        <label>Количество, {unit}<input ref={quantityRef} aria-label="Количество" inputMode="decimal" autoFocus autoComplete="off" placeholder={`остаток ${lot.balance}`} value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
        <label>Назначение / откуда<input aria-label="Назначение или откуда" autoComplete="off" placeholder="проект или фамилия" value={who} onChange={(event) => setWho(event.target.value)} /></label>
        <button className="primary" disabled={disabled} title="Enter">Списать</button>
        <button type="button" className="secondary" disabled={disabled || !(Number(lot.written_off) > 0)} title={`Списано ${lot.written_off} ${unit}`} onClick={giveBack}>Вернуть</button>
      </form>
      <form className="scan-actions" aria-label="Перемещение" onSubmit={(event) => { event.preventDefault(); move(); }}>
        <label>Новое место<input aria-label="Новое место" autoComplete="off" placeholder="12C7" value={place} onChange={(event) => setPlace(event.target.value)} /></label>
        <button className="secondary" disabled={disabled}>Переместить</button>
      </form>
      <UndoLast lot={lot} disabled={disabled} mutate={mutate} eager onUndone={(updated, label) => {
        setError("");
        setDone(`Отменено: ${label}`);
        onChanged(updated);
      }} />
      <div className="scan-label">
        <button type="button" className="secondary small" aria-expanded={label} onClick={() => setLabel((open) => !open)}>{label ? "Скрыть этикетку" : "Этикетка…"}</button>
        {label && <LotLabel key={lot.version} lotId={lot.id} />}
      </div>
      <p className="page-sub">Enter в количестве — списать · Enter в месте — переместить · Esc — закрыть</p>
    </>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {done && <div className="success-banner" role="status">{done}</div>}
  </div>;
}

function shortTime(iso: string) {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "" : at.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

/** «Отменить последнее» with what it will take back (own write-off, return or move of
 *  the last 10 minutes; the server appends a compensating entry). `eager` — the line is read at once and after every
 *  change of the lot (scan card); otherwise the first click reads it and the second one undoes (lot cards in the table). */
export function UndoLast({ lot, disabled, mutate, eager = false, onUndone }: {
  lot: WarehouseLot;
  disabled: boolean;
  mutate: Mutate;
  eager?: boolean;
  onUndone?: (lot: WarehouseLot, label: string) => void;
}) {
  const [preview, setPreview] = useState<WarehouseUndoPreview | null>(null);
  const [error, setError] = useState("");

  function load() {
    setError("");
    warehouseApi.undoPreview(lot.id).then(setPreview, () => setPreview(null));
  }

  useEffect(() => {
    setPreview(null);
    if (eager) load();
  }, [lot.id, lot.version]);

  async function undo() {
    setError("");
    const box: { lot?: WarehouseLot; label?: string } = {};
    try {
      await mutate(lot.id, async () => {
        const answer = await warehouseApi.undo(lot.id, lot.version);
        box.lot = answer.lot;
        box.label = answer.undone;
      });
    } catch (reason) {
      setError(failure(reason, "Отмена не выполнена"));
      return;
    }
    if (box.lot) onUndone?.(box.lot, box.label ?? "");
  }

  if (!preview) {
    return eager ? null : <button type="button" className="secondary small" disabled={disabled} onClick={load}>Отменить последнее…</button>;
  }
  return <div className="scan-undo" role="group" aria-label="Отмена последнего движения">
    {preview.undo ? <>
      <span>Последнее: <strong>{preview.undo.label}</strong>{shortTime(preview.undo.occurred_at) ? ` в ${shortTime(preview.undo.occurred_at)}` : ""}</span>
      <button type="button" className="secondary small" disabled={disabled} onClick={() => void undo()}>Отменить последнее</button>
    </> : <span>{preview.reason}</span>}
    {!eager && <button type="button" className="link-button" onClick={() => setPreview(null)}>Скрыть</button>}
    {error && <span className="danger-text" role="alert">{error}</span>}
  </div>;
}
