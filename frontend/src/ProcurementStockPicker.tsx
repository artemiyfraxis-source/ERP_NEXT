import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, failureText, type ProcurementLine } from "./api";

/* «Со склада»: позицию берём с нашего склада, а не покупаем. Закупщик выбирает партию, указывает количество и
   резервирует её; полное покрытие закрывает строку без денег, частичное уменьшает «КОЛ-ВО (факт)» до остатка.
   «Есть на складе, без ГРН» — то же без партии и без резерва; ГРН привязывается потом («Привязать ГРН»). */

export type StockCover = {
  id: string; line_id: string; label: string; lot_id: string | null; warehouse: string; grn: string; package: string;
  storage_location: string; unit: string; quantity: string; consumed: string; consumed_by: string | null;
  active: boolean; created_at: string; created_by: string; released_at: string | null;
};

export type StockLot = {
  id: string; version: number; warehouse: string; grn: string; package: string; article: string; series: string;
  name: string; item_type: string; purpose: string; unit: string; storage_location: string;
  balance: string; reserved: string; available: string; unit_matches: boolean;
  holders: StockCover[]; reserve_note: string;
};

type Candidates = { line: ProcurementLine; query: string; lots: StockLot[]; covers: StockCover[] };

/** Поля строки, которые добавил склад: сколько закрыто со склада, сколько осталось купить и сколько закрыто
 *  «Есть на складе, без ГРН» и ждёт «Привязать ГРН» (последнее отдают только «Мои позиции»). */
export type StockFields = { stock_covered?: string | null; to_buy?: string | null; stock_without_grn?: string | null };

export const stockApi = {
  candidates: (lineId: string, q?: string) =>
    api<Candidates>(`/api/procurement/${lineId}/stock-candidates${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  cover: (lineId: string, version: number, lotId: string, quantity: string) =>
    api<{ line: ProcurementLine; cover: StockCover }>(`/api/procurement/${lineId}/stock-cover`, {
      method: "POST", body: JSON.stringify({ version, lot_id: lotId, quantity }),
    }),
  coverWithoutLot: (lineId: string, version: number, quantity: string, warehouse: string) =>
    api<{ line: ProcurementLine; cover: StockCover }>(`/api/procurement/${lineId}/stock-cover`, {
      method: "POST", body: JSON.stringify({ version, quantity, warehouse }),
    }),
  bind: (lineId: string, version: number, coverId: string, lotId: string) =>
    api<{ line: ProcurementLine; cover: StockCover }>(`/api/procurement/${lineId}/stock-cover/${coverId}/bind`, {
      method: "POST", body: JSON.stringify({ version, lot_id: lotId }),
    }),
  release: (lineId: string, version: number, coverId?: string) =>
    api<{ line: ProcurementLine; released: StockCover[] }>(`/api/procurement/${lineId}/stock-cover/release`, {
      method: "POST", body: JSON.stringify({ version, cover_id: coverId }),
    }),
};

const STALE = "Строка изменилась — таблица обновлена, откройте её действие ещё раз.";

export function coveredOf(line: ProcurementLine): number {
  return Number((line as ProcurementLine & StockFields).stock_covered ?? 0) || 0;
}

/** «Готово · без ГРН»: строка закрыта «Есть на складе, без ГРН» (без оплаты), ГРН к ней ещё не привязан. */
export function waitsForGrn(line: ProcurementLine): boolean {
  const withoutGrn = Number((line as ProcurementLine & StockFields).stock_without_grn ?? 0) || 0;
  return line.status === "Готово" && !line.paid_at && withoutGrn > 0;
}

export function toBuyOf(line: ProcurementLine): string {
  return (line as ProcurementLine & StockFields).to_buy ?? line.calculated_quantity;
}

const num = (value: string) => Number(String(value).replace(",", ".")) || 0;
const fmt = (value: number) => String(Math.round(value * 1e6) / 1e6).replace(".", ",");

export function ProcurementStockPicker({ line, disabled, bindOnly = false, onDone, onClose }: {
  line: ProcurementLine;
  disabled: boolean;
  /** Только «Привязать ГРН» (строка «Готово · без ГРН»): покрытие без ГРН выбрано сразу, новый резерв не ставится. */
  bindOnly?: boolean;
  /** Резерв поставлен или строка устарела — таблица перечитывает строки. */
  onDone: (message: string) => void;
  /** Своя кнопка «Закрыть»; в панели стадии её нет — там закрывает заголовок панели. */
  onClose?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [lots, setLots] = useState<StockLot[]>([]);
  const [covers, setCovers] = useState<StockCover[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [lotId, setLotId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [busy, setBusy] = useState(false);
  // «Привязать ГРН»: покрытие без партии, которому сейчас выбирают партию (всё его количество, не часть).
  const [bindId, setBindId] = useState("");
  const toBuy = num(toBuyOf(line));
  const lotless = covers.filter((cover) => cover.active && !cover.lot_id);
  const binding = lotless.find((cover) => cover.id === bindId);

  async function search(q?: string) {
    setLoading(true);
    setError("");
    try {
      const result = await stockApi.candidates(line.id, q);
      setLots(result.lots);
      setCovers(result.covers);
      const first = bindOnly && !bindId ? result.covers.find((cover) => cover.active && !cover.lot_id) : undefined;
      if (first) { setBindId(first.id); setQuantity(first.quantity.replace(".", ",")); }
      if (q === undefined) setQuery(result.query);
      setLotId("");
    } catch (reason) {
      setError(failureText(reason, "Склад не ответил"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void search(); }, [line.id]);

  function pick(lot: StockLot) {
    if (!lot.unit_matches) return;
    setLotId(lot.id);
    setQuantity(binding ? binding.quantity.replace(".", ",") : fmt(Math.min(num(lot.available), toBuy)));
  }

  function startBind(coverId: string) {
    const next = bindId === coverId ? "" : coverId;
    setBindId(next);
    setLotId("");
    setQuantity("");
  }

  async function reserve() {
    const lot = lots.find((item) => item.id === lotId);
    if (!lot) return;
    setBusy(true);
    setError("");
    try {
      if (binding) {
        const result = await stockApi.bind(line.id, line.version, binding.id, lot.id);
        onDone(`${line.legacy_id}: ГРН ${result.cover.grn} привязан, резерв ${result.cover.quantity} ${result.cover.unit}`);
        return;
      }
      const result = await stockApi.cover(line.id, line.version, lot.id, quantity.trim().replace(",", "."));
      onDone(`${line.legacy_id}: со склада ${result.cover.quantity} ${result.cover.unit} (ГРН ${result.cover.grn})`);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        setError(`Строка изменилась — таблица обновлена, откройте ${bindOnly ? "«Привязать ГРН»" : "«Со склада»"} ещё раз.`);
        onDone("");
      } else {
        setError(failureText(reason, "Резерв не поставлен"));
      }
    } finally {
      setBusy(false);
    }
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    void search(query.trim());
  }

  const quantityOk = num(quantity) > 0;
  const held = covers.filter((cover) => cover.active && cover.lot_id);
  const chosen = lots.find((item) => item.id === lotId);
  const short = binding && chosen && num(chosen.available) < num(binding.quantity);
  return (
    <div className="stock-picker">
      <form className={bindOnly ? "stock-search bind-search" : "stock-search"} onSubmit={submitSearch}>
        {!bindOnly && <strong>Со склада · {line.legacy_id}</strong>}
        {/* Фокус уходит в открытую панель: иначе строка с фокусом держит раскрытые действия, и при первом клике
            в панели строка схлопывается, а клик проходит мимо кнопки. */}
        <input aria-label="Поиск на складе" autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="артикул или наименование" />
        <button type="submit" className="secondary small" disabled={loading}>Найти</button>
        {!bindOnly && <small>К закупке: {fmt(toBuy)} {line.calculated_unit}{coveredOf(line) > 0 ? ` · уже со склада: ${fmt(coveredOf(line))}` : ""}</small>}
        {onClose && <button type="button" className="secondary small" onClick={onClose}>Закрыть</button>}
      </form>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {held.length > 0 && (
        <small>Уже зарезервировано: {held.map((cover) => `${cover.quantity} ${cover.unit} (ГРН ${cover.grn}, ${cover.warehouse})`).join("; ")}</small>
      )}
      {lotless.map((cover) => (
        <div key={cover.id} className="stock-search">
          <small>Без ГРН: {cover.quantity} {cover.unit}{cover.warehouse ? ` · ${cover.warehouse}` : ""}{bindId === cover.id ? " — выберите партию ниже" : ""}</small>
          {!bindOnly && <button type="button" className="secondary small" disabled={disabled} onClick={() => startBind(cover.id)}>{bindId === cover.id ? "Отменить привязку" : "Привязать ГРН"}</button>}
        </div>
      ))}
      {bindOnly && !loading && !lotless.length && <div className="empty">Без ГРН ничего не осталось — ГРН уже привязан.</div>}
      <div className="inwork-scroll">
        <table className="inwork-table stock-lots">
          <thead>
            <tr><th aria-label="Выбор" /><th>Склад</th><th>ГРН</th><th>Пакет</th><th>Место</th><th>Наименование / артикул</th><th>Доступно</th><th>Ед.</th></tr>
          </thead>
          <tbody>
            {lots.map((lot) => (
              <tr key={lot.id} className={lot.unit_matches ? undefined : "muted"} title={lot.unit_matches ? lot.reserve_note || undefined : `Единица склада «${lot.unit}» не совпадает с «${line.calculated_unit}»`}>
                <td><input type="radio" name={`lot-${line.id}`} aria-label={`Партия ${lot.grn}`} checked={lotId === lot.id} disabled={!lot.unit_matches || disabled} onChange={() => pick(lot)} /></td>
                <td>{lot.warehouse}</td>
                <td>{lot.grn}</td>
                <td>{lot.package}</td>
                <td>{lot.storage_location}</td>
                <td>{lot.name}{lot.article ? <small> · {lot.article}</small> : null}{!lot.unit_matches && <small> · другая единица</small>}</td>
                <td className="num">{lot.available}</td>
                <td>{lot.unit}</td>
              </tr>
            ))}
            {!lots.length && <tr><td colSpan={8} className="empty">{loading ? "Ищем на складе…" : "На складе ничего не найдено — измените запрос."}</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="stock-reserve">
        <label>Количество<input aria-label="Количество со склада" inputMode="decimal" value={quantity} disabled={!lotId || Boolean(binding)} onChange={(event) => setQuantity(event.target.value)} /></label>
        {binding ? (
          <button type="button" className="primary small" disabled={disabled || busy || !lotId || Boolean(short)} title={short ? `На партии доступно ${chosen?.available} — нужно ${binding.quantity}` : undefined} onClick={() => void reserve()}>
            {busy ? "Привязываем…" : `Привязать ГРН${chosen ? ` ${chosen.grn}` : ""}`}
          </button>
        ) : !bindOnly && (
          <button type="button" className="primary small" disabled={disabled || busy || !lotId || !quantityOk} onClick={() => void reserve()}>{busy ? "Резервируем…" : "Зарезервировать"}</button>
        )}
      </div>
    </div>
  );
}

/** «Есть на складе, без ГРН»: позиция есть, но партию закупщик не указывает — строка закрывается на это количество
 *  без резерва (всё — «Готово» без денег, часть — остаток к закупке). Склад — по желанию. */
export function ProcurementNoLotCover({ line, disabled, onDone, onClose }: {
  line: ProcurementLine;
  disabled: boolean;
  onDone: (message: string) => void;
  onClose: () => void;
}) {
  const toBuy = num(toBuyOf(line));
  const [quantity, setQuantity] = useState(fmt(toBuy));
  const [warehouse, setWarehouse] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const amount = num(quantity);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await stockApi.coverWithoutLot(line.id, line.version, quantity.trim().replace(",", "."), warehouse);
      onDone(`${line.legacy_id}: есть на складе ${result.cover.quantity} ${result.cover.unit}, без ГРН`);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        setError(STALE);
        onDone("");
      } else {
        setError(failureText(reason, "Не отмечено"));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stock-picker" aria-label={`Есть на складе, без ГРН ${line.legacy_id}`} onSubmit={(event) => void submit(event)}>
      <div className="stock-search">
        <strong>Есть на складе, без ГРН · {line.legacy_id}</strong>
        <small>К закупке: {fmt(toBuy)} {line.calculated_unit} · без резерва на партии</small>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="stock-reserve">
        <label>Количество<input aria-label="Количество без ГРН" autoFocus inputMode="decimal" value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
        <label>Склад<select aria-label="Склад без ГРН" style={{ height: 26, padding: "0 6px" }} value={warehouse} onChange={(event) => setWarehouse(event.target.value)}>
          <option value="">не указан</option>
          <option value="ЦАР">ЦАР</option>
          <option value="ЖУК">ЖУК</option>
        </select></label>
        <button type="submit" className="primary small" disabled={disabled || busy || amount <= 0}>
          {busy ? "Отмечаем…" : "Отметить со склада"}
        </button>
        <button type="button" className="secondary small" onClick={onClose}>Закрыть</button>
      </div>
    </form>
  );
}
