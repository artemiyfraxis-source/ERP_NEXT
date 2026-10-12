import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { warehouseApi, type WarehouseLot, type WarehouseScan as ScanAnswer } from "./api";

type Mutate = (key: string, operation: () => Promise<unknown>) => Promise<void>;

function failure(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

function lotLabel(lot: WarehouseLot) {
  return `${lot.grn}${lot.package ? ` / ${lot.package}` : ""}`;
}

/** Stage option warehouse.scan (Roller_Buyer /stock and /tsd): one big field at the top of «Склад». A hardware scanner
 *  types the code and Enter; a ГРН opens the lot card, a place code lists what lies there. After every action the
 *  field takes the focus back, so the next scan goes straight in. */
export function WarehouseScanBar({ readOnly, disabled, mutate }: { readOnly: boolean; disabled: boolean; mutate: Mutate }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [code, setCode] = useState("");
  const [answer, setAnswer] = useState<ScanAnswer | null>(null);
  const [lot, setLot] = useState<WarehouseLot | null>(null);
  const [error, setError] = useState("");
  const [searching, setSearching] = useState(false);
  // «Назначение / откуда» is the same for a run of scans (one project, one person): kept between cards.
  const [who, setWho] = useState("");

  function refocus() {
    window.setTimeout(() => { inputRef.current?.focus(); inputRef.current?.select(); }, 0);
  }

  function closeAll() {
    setAnswer(null);
    setLot(null);
    setError("");
    refocus();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = code.trim();
    if (!text || searching) return;
    setSearching(true);
    setError("");
    try {
      const found = await warehouseApi.scan(text);
      if (found.kind === "lot" && found.lots.length === 1) {
        setAnswer(null);
        setLot(found.lots[0]);
      } else {
        setAnswer(found);
        setLot(null);
      }
    } catch (reason) {
      setAnswer(null);
      setLot(null);
      setError(failure(reason, "Скан не выполнен"));
      refocus();
    } finally {
      setSearching(false);
    }
  }

  function changed(updated: WarehouseLot) {
    setLot(updated);
    setAnswer((current) => current && { ...current, lots: current.lots.map((item) => item.id === updated.id ? updated : item) });
    refocus();
  }

  return <section className="scan-zone" aria-label="Скан">
    <form className="scan-bar" role="search" onSubmit={(event) => void submit(event)}>
      <input
        ref={inputRef}
        className="scan-input"
        aria-label="Скан / ГРН / место"
        placeholder="Скан / ГРН / место — Enter"
        autoFocus
        autoComplete="off"
        spellCheck={false}
        value={code}
        onChange={(event) => setCode(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Escape") { setCode(""); closeAll(); } }}
      />
      <button className="secondary" disabled={searching || !code.trim()}>{searching ? "Ищем…" : "Найти"}</button>
    </form>
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
  useEffect(() => { setError(""); setDone(""); }, [lot.id]);

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
      <p className="page-sub">Enter в количестве — списать · Enter в месте — переместить · Esc — закрыть</p>
    </>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {done && <div className="success-banner" role="status">{done}</div>}
  </div>;
}
